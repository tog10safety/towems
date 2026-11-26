package online.mrmsystem.towems;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.util.Log;
import android.webkit.CookieManager;

import androidx.core.app.NotificationCompat;
import androidx.core.content.ContextCompat;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.TimeZone;

/**
 * Reports the vehicle's position while the driver's run is open.
 *
 * The driver page records GPS from the WebView, which is fine while the screen
 * is on and useless the moment Android suspends a backgrounded WebView - the tab
 * stops firing location callbacks and the board loses the convoy with the phone
 * in a pocket. Background-location permission alone cannot fix that, because it
 * only says what the app *may* do, not that anything is still awake to do it.
 *
 * So the fixes come from a foreground service with the location type: the
 * platform keeps the process alive, the GPS keeps delivering, and each batch is
 * posted with the phone's long-lived token - the same narrow credential the
 * alert watcher uses - so a run that outlasts the one-hour web session keeps
 * reporting.
 *
 * The service records nothing unless the server says a run is open for this
 * account, which is what stops it draining the battery on a parked phone. What
 * it may report is decided on the server: the run must be assigned to this
 * driver, so the token cannot move anybody else's vehicle.
 */
public class TrackingService extends Service implements LocationListener {

    private static final String TAG = "TOWEMS/Tracking";
    private static final String BASE_URL = AlertService.BASE_URL;
    private static final String SESSION_COOKIE = "toms_session";
    private static final String CSRF_COOKIE = "csrftoken";

    /** The watcher's preferences; the token and the install id live there. */
    private static final String WATCHER_PREFS = "towems_alerts";
    private static final String TOKEN_KEY = "device_token";

    private static final String CHANNEL_TRACKING = "towems_tracking";
    private static final int SERVICE_NOTIFICATION_ID = 3;

    /** Nothing is open: asking once a minute is enough to notice a new run. */
    private static final long IDLE_MS = 60_000L;
    /** A run is open: remember the run and keep the fix cadence honest. */
    private static final long RUN_MS = 15_000L;
    /** After a failed call, wait a little before asking again. */
    private static final long RETRY_MS = 30_000L;

    /** One batch is four breadcrumbs, matching the web page's own batching. */
    private static final int BATCH = 4;
    /** A tunnel is not data loss, but it is not worth an unbounded queue either. */
    private static final int MAX_QUEUE = 200;

    private static final long MIN_TIME_MS = 10_000L;
    private static final float MIN_DISTANCE_M = 5f;

    private HandlerThread worker;
    private Handler handler;
    private SharedPreferences prefs;
    private LocationManager locations;
    private final List<JSONObject> queue = new ArrayList<>();
    private boolean listening;
    private long runId;
    private long missionId;
    private boolean showingActive;

    private final SimpleDateFormat stamp =
            new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US);

    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            long next = IDLE_MS;
            try {
                next = tickOnce();
            } catch (Exception error) {
                Log.w(TAG, "tracking check failed", error);
                next = RETRY_MS;
            }
            handler.postDelayed(this, next);
        }
    };

    /** Start the tracker only where the person already granted location. */
    public static void startIfPermitted(Context context) {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION)
                != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        try {
            ContextCompat.startForegroundService(
                    context, new Intent(context, TrackingService.class));
        } catch (RuntimeException refused) {
            // Android 12+ refuses a location service started from the background;
            // the next time the app is opened the tracker starts normally.
            Log.i(TAG, "location service not started: " + refused.getClass().getSimpleName());
        }
    }

    @Override
    public void onCreate() {
        super.onCreate();
        stamp.setTimeZone(TimeZone.getTimeZone("UTC"));
        worker = new HandlerThread("towems-tracking");
        worker.start();
        handler = new Handler(worker.getLooper());
        prefs = getSharedPreferences(WATCHER_PREFS, Context.MODE_PRIVATE);
        locations = (LocationManager) getSystemService(Context.LOCATION_SERVICE);
        createChannel();
        startForegroundNotification(idleNotification(getString(R.string.tracking_idle_text)));
        handler.post(tick);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // The notification is the only thing the person sees; it must be there
        // even when the service was restarted by the system with no intent.
        if (!showingActive) {
            startForegroundNotification(idleNotification(getString(R.string.tracking_idle_text)));
        }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        stopUpdates();
        if (handler != null) {
            handler.removeCallbacks(tick);
        }
        if (worker != null) {
            worker.quitSafely();
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager == null) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_TRACKING,
                getString(R.string.tracking_channel_name),
                NotificationManager.IMPORTANCE_LOW);
        channel.setDescription(getString(R.string.tracking_channel_description));
        channel.setShowBadge(false);
        manager.createNotificationChannel(channel);
    }

    private void startForegroundNotification(Notification notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(SERVICE_NOTIFICATION_ID, notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION);
        } else {
            startForeground(SERVICE_NOTIFICATION_ID, notification);
        }
    }

    /** One round trip: is a run open, and if so, is the last batch sent? */
    private long tickOnce() {
        String token = prefs.getString(TOKEN_KEY, null);
        String cookie = CookieManager.getInstance().getCookie(BASE_URL);
        if ((token == null || token.isEmpty()) && cookie == null) {
            stopUpdates();
            showIdle();
            return IDLE_MS;
        }

        JSONObject state = request("GET", "/api/auth/devices/tracking/", null, token, cookie);
        if (state == null) {
            return RETRY_MS;
        }
        long openRun = state.optLong("run", 0);
        if (openRun <= 0) {
            stopUpdates();
            queue.clear();
            showIdle();
            return IDLE_MS;
        }

        runId = openRun;
        missionId = state.optLong("mission", 0);
        startUpdates();
        showActive();
        flush(token, cookie);
        return RUN_MS;
    }

    private void startUpdates() {
        if (listening || locations == null) {
            return;
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
                != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        try {
            if (locations.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                locations.requestLocationUpdates(
                        LocationManager.GPS_PROVIDER, MIN_TIME_MS, MIN_DISTANCE_M,
                        this, worker.getLooper());
            }
            if (locations.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) {
                locations.requestLocationUpdates(
                        LocationManager.NETWORK_PROVIDER, MIN_TIME_MS * 3, MIN_DISTANCE_M * 5,
                        this, worker.getLooper());
            }
            listening = true;
            Log.i(TAG, "recording this vehicle's position");
        } catch (SecurityException | IllegalArgumentException error) {
            Log.w(TAG, "location updates refused", error);
        }
    }

    private void stopUpdates() {
        if (!listening || locations == null) {
            return;
        }
        try {
            locations.removeUpdates(this);
            Log.i(TAG, "run closed; location updates stopped");
        } catch (SecurityException error) {
            Log.w(TAG, "could not stop location updates", error);
        }
        listening = false;
    }

    @Override
    public void onLocationChanged(Location location) {
        if (location == null || runId <= 0) {
            return;
        }
        try {
            JSONObject row = new JSONObject();
            row.put("latitude", location.getLatitude());
            row.put("longitude", location.getLongitude());
            row.put("accuracy_m", location.hasAccuracy() ? location.getAccuracy() : JSONObject.NULL);
            row.put("speed_mps", location.hasSpeed() ? location.getSpeed() : JSONObject.NULL);
            row.put("heading_deg", location.hasBearing() ? location.getBearing() : JSONObject.NULL);
            row.put("recorded_at", stamp.format(new Date(location.getTime())));
            synchronized (queue) {
                queue.add(row);
                while (queue.size() > MAX_QUEUE) {
                    queue.remove(0);
                }
                if (queue.size() < BATCH) {
                    return;
                }
            }
            flush(prefs.getString(TOKEN_KEY, null),
                    CookieManager.getInstance().getCookie(BASE_URL));
        } catch (Exception error) {
            Log.w(TAG, "could not record a fix", error);
        }
    }

    /** Send the queued breadcrumbs; keep them if the network is not there. */
    private void flush(String token, String cookie) {
        List<JSONObject> batch;
        synchronized (queue) {
            if (queue.isEmpty() || runId <= 0) {
                return;
            }
            batch = new ArrayList<>(queue);
            queue.clear();
        }
        try {
            JSONArray pings = new JSONArray();
            for (JSONObject row : batch) {
                pings.put(row);
            }
            JSONObject body = new JSONObject();
            body.put("run", runId);
            body.put("pings", pings);
            JSONObject reply = request(
                    "POST", "/api/auth/devices/tracking/", body, token, cookie);
            if (reply == null) {
                keep(batch);
                return;
            }
            Log.i(TAG, "sent " + reply.optInt("accepted", 0) + " position(s)");
        } catch (Exception error) {
            Log.w(TAG, "could not send positions", error);
            keep(batch);
        }
    }

    private void keep(List<JSONObject> batch) {
        synchronized (queue) {
            batch.addAll(queue);
            queue.clear();
            queue.addAll(batch);
            while (queue.size() > MAX_QUEUE) {
                queue.remove(0);
            }
        }
    }

    private void showIdle() {
        boolean wasActive = showingActive;
        showingActive = false;
        runId = 0;
        if (wasActive) {
            updateNotification(idleNotification(getString(R.string.tracking_idle_text)));
        }
    }

    private void showActive() {
        if (showingActive) {
            return;
        }
        showingActive = true;
        updateNotification(activeNotification());
    }

    private void updateNotification(Notification notification) {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) {
            manager.notify(SERVICE_NOTIFICATION_ID, notification);
        }
    }

    private Notification idleNotification(String text) {
        return baseNotification().setContentText(text).build();
    }

    private Notification activeNotification() {
        return baseNotification()
                .setContentText(getString(R.string.tracking_active_text))
                .setSubText(getString(R.string.tracking_active_subtext))
                .build();
    }

    private NotificationCompat.Builder baseNotification() {
        return new NotificationCompat.Builder(this, CHANNEL_TRACKING)
                .setSmallIcon(R.mipmap.ic_launcher)
                .setContentTitle(getString(R.string.tracking_title))
                .setOngoing(true)
                .setSilent(true)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .setContentIntent(openApp());
    }

    private PendingIntent openApp() {
        Intent intent = new Intent(this, MainActivity.class);
        intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return PendingIntent.getActivity(
                this,
                3,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    /**
     * A JSON call with the phone's token, falling back to the WebView's session.
     *
     * The token is the normal path; the session covers the first hour on a build
     * whose server does not yet answer the device endpoint.
     */
    private JSONObject request(String method, String path, JSONObject body, String token,
            String cookie) {
        HttpURLConnection connection = null;
        try {
            byte[] payload = body == null
                    ? null
                    : body.toString().getBytes(StandardCharsets.UTF_8);
            connection = (HttpURLConnection) new URL(BASE_URL + path).openConnection();
            connection.setRequestMethod(method);
            connection.setConnectTimeout(15_000);
            connection.setReadTimeout(20_000);
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("User-Agent", "TOWEMS-Android/1.0");
            if (token != null && !token.isEmpty()) {
                connection.setRequestProperty("Authorization", "Device " + token);
            }
            if ("POST".equals(method)) {
                // Session auth checks CSRF on POST; the token path ignores it.
                if (cookie != null) {
                    connection.setRequestProperty("Cookie", cookie);
                    String csrf = cookieValue(cookie, CSRF_COOKIE);
                    if (csrf != null) {
                        connection.setRequestProperty("X-CSRFToken", csrf);
                        connection.setRequestProperty("Referer", BASE_URL + "/");
                    }
                }
                connection.setDoOutput(true);
                connection.setRequestProperty(
                        "Content-Type", "application/json; charset=utf-8");
                connection.setFixedLengthStreamingMode(payload.length);
                try (OutputStream out = connection.getOutputStream()) {
                    out.write(payload);
                }
            } else if (cookie != null) {
                connection.setRequestProperty("Cookie", cookie);
            }

            int code = connection.getResponseCode();
            if (code != 200) {
                Log.i(TAG, method + " " + path + " answered " + code);
                return null;
            }
            return new JSONObject(read(connection));
        } catch (Exception error) {
            Log.w(TAG, "request failed: " + path, error);
            return null;
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    private String read(HttpURLConnection connection) throws Exception {
        try (InputStream body = connection.getInputStream()) {
            byte[] buffer = new byte[64 * 1024];
            StringBuilder text = new StringBuilder();
            int read;
            while ((read = body.read(buffer)) > 0) {
                text.append(new String(buffer, 0, read, StandardCharsets.UTF_8));
            }
            return text.toString();
        }
    }

    private String cookieValue(String cookie, String name) {
        if (cookie == null) {
            return null;
        }
        for (String part : cookie.split(";")) {
            String item = part.trim();
            int equals = item.indexOf('=');
            if (equals > 0 && item.substring(0, equals).equals(name)) {
                return item.substring(equals + 1);
            }
        }
        return null;
    }

    // LocationListener: the three callbacks older platforms still require.
    @Override
    public void onStatusChanged(String provider, int status, Bundle extras) {
    }

    @Override
    public void onProviderEnabled(String provider) {
    }

    @Override
    public void onProviderDisabled(String provider) {
    }
}
