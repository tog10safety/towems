package online.mrmsystem.towems;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.util.Log;
import android.webkit.CookieManager;

import androidx.core.app.NotificationCompat;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.Set;
import java.util.UUID;

/**
 * Watches for TOWEMS alerts while the app is not on screen.
 *
 * It answers the same question the web bell asks - the notification
 * {@code pulse} - but from a foreground service, because the app has no Web
 * Push endpoint of its own.
 *
 * A Django session lasts an hour, which is right for a browser and useless for
 * a watcher that must wake somebody at 03:00. So the service exchanges the live
 * WebView session once for a narrow token that the server keeps for days: it
 * reads the pulse and the unread count and nothing else, it cannot write, and
 * the server ends it when that session is signed out or the password changes.
 * The session itself keeps its one-hour life; nothing here weakens the web
 * login, and the password and authenticator seed never leave the sign-in form.
 *
 * Chat message text is never read or displayed here; a message alert says only
 * that something is waiting.
 */
public class AlertService extends Service {

    private static final String TAG = "TOWEMS/Alerts";
    public static final String BASE_URL = "https://mrm-system.online";
    /** The deployment renames Django's cookie (SESSION_COOKIE_NAME in settings). */
    private static final String SESSION_COOKIE = "toms_session";
    /** The SPA reads this cookie to set the X-CSRFToken header; so do we. */
    private static final String CSRF_COOKIE = "csrftoken";

    private static final String TOKEN_KEY = "device_token";
    /** The session the stored token was minted from. */
    private static final String TOKEN_SESSION_KEY = "token_session";
    private static final String DEVICE_ID_KEY = "device_id";

    private static final long POLL_MS = 20_000L;
    private static final long SIGNED_OUT_MS = 300_000L;

    private static final String CHANNEL_ALERTS = "towems_alerts";
    private static final String CHANNEL_MESSAGES = "towems_messages";
    private static final String CHANNEL_SERVICE = "towems_service";

    private static final int SERVICE_NOTIFICATION_ID = 1;
    private static final int MESSAGES_NOTIFICATION_ID = 1002;
    private static final int INBOX_NOTIFICATION_ID = 1001;

    /** MainActivity keeps this true while a TOWEMS screen is visible. */
    public static volatile boolean appVisible = false;

    private HandlerThread worker;
    private Handler handler;
    private SharedPreferences prefs;
    private final Set<Long> seen = new HashSet<>();
    /** HTTP status of the last request; 0 means the request never got a reply. */
    private int lastStatus;

    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            long next = POLL_MS;
            try {
                next = pollOnce();
            } catch (Exception error) {
                Log.w(TAG, "poll failed", error);
            }
            handler.postDelayed(this, next);
        }
    };

    @Override
    public void onCreate() {
        super.onCreate();
        // The poll makes network calls, which Android forbids on the main thread;
        // a dedicated looper keeps the service off the UI thread entirely.
        worker = new HandlerThread("towems-alerts");
        worker.start();
        handler = new Handler(worker.getLooper());
        prefs = getSharedPreferences("towems_alerts", Context.MODE_PRIVATE);
        createChannels();
        startWatching();
        handler.post(tick);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
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

    private void createChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager == null) {
            return;
        }
        NotificationChannel alerts = new NotificationChannel(
                CHANNEL_ALERTS, "Operational alerts", NotificationManager.IMPORTANCE_HIGH);
        alerts.setDescription("Abort orders, emergencies, movement tasks and approvals.");
        manager.createNotificationChannel(alerts);

        NotificationChannel messages = new NotificationChannel(
                CHANNEL_MESSAGES, "Messages", NotificationManager.IMPORTANCE_DEFAULT);
        messages.setDescription("A message is waiting. The text stays in TOWEMS.");
        manager.createNotificationChannel(messages);

        NotificationChannel service = new NotificationChannel(
                CHANNEL_SERVICE, "Alert watcher", NotificationManager.IMPORTANCE_LOW);
        service.setDescription("Keeps TOWEMS able to reach this phone.");
        service.setShowBadge(false);
        manager.createNotificationChannel(service);
    }

    private void startWatching() {
        Notification notification = new NotificationCompat.Builder(this, CHANNEL_SERVICE)
                .setSmallIcon(R.mipmap.ic_launcher)
                .setContentTitle("TOWEMS is watching for alerts")
                .setContentText("Notifications arrive while the app is closed.")
                .setOngoing(true)
                .setSilent(true)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .setContentIntent(openIntent("/notifications", 0))
                .build();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(SERVICE_NOTIFICATION_ID, notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        } else {
            startForeground(SERVICE_NOTIFICATION_ID, notification);
        }
    }

    /** One round trip to the server. Returns how long to wait before the next. */
    private long pollOnce() {
        String cookie = CookieManager.getInstance().getCookie(BASE_URL);
        String session = cookieValue(cookie, SESSION_COOKIE);
        String token = prefs.getString(TOKEN_KEY, null);

        // Only a live session can mint a token, and it lives an hour. Re-minting
        // whenever the session changes keeps the token tied to the sign-in that
        // created it, so signing out still stops these alerts; a token minted by
        // an earlier session keeps the watcher alive after that session expires.
        if (session != null && !session.equals(prefs.getString(TOKEN_SESSION_KEY, null))) {
            String renewed = mint(cookie, session);
            if (renewed != null) {
                token = renewed;
            }
        }
        if (token == null) {
            return SIGNED_OUT_MS;
        }

        long since = prefs.getLong("latest_id", 0);
        JSONObject pulse = getJson("/api/notifications/pulse/?since=" + since, cookie, token);
        if (pulse == null && (lastStatus == 401 || lastStatus == 403)) {
            // The token was revoked (sign-out, password change) or expired.
            forgetToken();
            token = session == null ? null : mint(cookie, session);
            if (token == null) {
                return SIGNED_OUT_MS;
            }
            pulse = getJson("/api/notifications/pulse/?since=" + since, cookie, token);
        }
        if (pulse == null) {
            // No reply at all is a network blip: retry on the normal interval.
            return lastStatus == 0 ? POLL_MS : SIGNED_OUT_MS;
        }

        boolean firstRun = !prefs.contains("latest_id");
        long latestId = pulse.optLong("latest_id", 0);
        JSONArray urgent = pulse.optJSONArray("urgent");
        if (!firstRun && !appVisible && urgent != null) {
            for (int index = 0; index < urgent.length(); index += 1) {
                JSONObject row = urgent.optJSONObject(index);
                if (row == null) {
                    continue;
                }
                long id = row.optLong("id", 0);
                if (id == 0 || seen.contains(id)) {
                    continue;
                }
                seen.add(id);
                showAlert(row);
            }
        }
        // The first poll only establishes a baseline: opening the app for the
        // first time must not replay yesterday's inbox.
        rememberSeen(urgent);
        prefs.edit().putLong("latest_id", latestId).apply();

        int pending = pulse.optInt("pending_actions", 0);
        showPendingActions(pending);

        pollMessages(cookie, token);
        return POLL_MS;
    }

    private void pollMessages(String cookie, String token) {
        JSONObject summary = getJson("/api/messaging/summary/", cookie, token);
        if (summary == null) {
            return;
        }
        int unread = summary.optInt("unread_total", 0);
        int previous = prefs.getInt("unread_total", -1);
        prefs.edit().putInt("unread_total", unread).apply();
        if (unread <= 0) {
            cancel(MESSAGES_NOTIFICATION_ID);
            return;
        }
        if (appVisible || previous < 0 || unread <= previous) {
            return;
        }
        Notification notification = new NotificationCompat.Builder(this, CHANNEL_MESSAGES)
                .setSmallIcon(R.mipmap.ic_launcher)
                .setContentTitle(unread == 1 ? "New message" : "New messages")
                .setContentText("Open TOWEMS to read it.")
                .setAutoCancel(true)
                .setOnlyAlertOnce(true)
                .setNumber(unread)
                .setContentIntent(openIntent("/messages", 1))
                .build();
        notifyIfAllowed(MESSAGES_NOTIFICATION_ID, notification);
    }

    private void showAlert(JSONObject row) {
        String title = row.optString("title", "TOWEMS");
        String message = row.optString("message", "");
        String path = row.optString("action_url", "/notifications");
        boolean urgent = "urgent".equals(row.optString("priority", ""));
        NotificationCompat.Builder builder = new NotificationCompat.Builder(this, CHANNEL_ALERTS)
                .setSmallIcon(R.mipmap.ic_launcher)
                .setContentTitle(title.length() > 120 ? title.substring(0, 120) : title)
                .setStyle(new NotificationCompat.BigTextStyle().bigText(message))
                .setAutoCancel(true)
                .setPriority(urgent ? NotificationCompat.PRIORITY_MAX : NotificationCompat.PRIORITY_HIGH)
                .setCategory(urgent ? NotificationCompat.CATEGORY_ALARM : NotificationCompat.CATEGORY_MESSAGE)
                .setContentIntent(openIntent(path, (int) (row.optLong("id", 0) % 100000) + 10000));
        notifyIfAllowed((int) (row.optLong("id", 0) % 100000) + 10000, builder.build());
    }

    private void showPendingActions(int pending) {
        if (pending <= 0) {
            cancel(INBOX_NOTIFICATION_ID);
            return;
        }
        if (prefs.getInt("pending_actions", -1) == pending) {
            return;
        }
        prefs.edit().putInt("pending_actions", pending).apply();
        Notification notification = new NotificationCompat.Builder(this, CHANNEL_ALERTS)
                .setSmallIcon(R.mipmap.ic_launcher)
                .setContentTitle(pending == 1 ? "1 action is waiting" : pending + " actions are waiting")
                .setContentText("Approvals and movement tasks you still need to do.")
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setContentIntent(openIntent("/notifications?scope=action", 2))
                .build();
        notifyIfAllowed(INBOX_NOTIFICATION_ID, notification);
    }

    private void rememberSeen(JSONArray rows) {
        if (rows == null) {
            return;
        }
        for (int index = 0; index < rows.length(); index += 1) {
            JSONObject row = rows.optJSONObject(index);
            if (row != null) {
                seen.add(row.optLong("id", 0));
            }
        }
    }

    /**
     * Exchange the live WebView session for the watcher's long-lived token.
     *
     * This is the same authenticated POST the web app makes, sent with the
     * cookies the WebView already holds; the server checks the session, its CSRF
     * token and the first-sign-in gates exactly as it does for the browser.
     */
    private String mint(String cookie, String session) {
        String csrf = cookieValue(cookie, CSRF_COOKIE);
        if (session == null || csrf == null) {
            return null;
        }
        HttpURLConnection connection = null;
        try {
            JSONObject body = new JSONObject();
            body.put("device_id", deviceId());
            body.put("label", deviceLabel());
            byte[] payload = body.toString().getBytes(StandardCharsets.UTF_8);

            connection = (HttpURLConnection) new URL(BASE_URL + "/api/auth/devices/token/")
                    .openConnection();
            connection.setRequestMethod("POST");
            connection.setDoOutput(true);
            connection.setConnectTimeout(15_000);
            connection.setReadTimeout(20_000);
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("Cookie", cookie);
            // Session authentication checks CSRF on POST; the browser sends the
            // same two values, and the POST is not a privileged action by itself.
            connection.setRequestProperty("X-CSRFToken", csrf);
            connection.setRequestProperty("Referer", BASE_URL + "/");
            connection.setRequestProperty("User-Agent", "TOWEMS-Android/1.0");
            connection.setFixedLengthStreamingMode(payload.length);
            try (OutputStream out = connection.getOutputStream()) {
                out.write(payload);
            }

            int code = connection.getResponseCode();
            if (code != 200 && code != 201) {
                Log.i(TAG, "token exchange answered " + code);
                return null;
            }
            String token = new JSONObject(read(connection)).optString("token", "");
            if (token.isEmpty()) {
                return null;
            }
            prefs.edit()
                    .putString(TOKEN_KEY, token)
                    .putString(TOKEN_SESSION_KEY, session)
                    .apply();
            Log.i(TAG, "alert token renewed for this phone");
            return token;
        } catch (Exception error) {
            Log.w(TAG, "token exchange failed", error);
            return null;
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    private void forgetToken() {
        prefs.edit().remove(TOKEN_KEY).remove(TOKEN_SESSION_KEY).apply();
    }

    /** One random identifier per install, so re-minting rotates a single row. */
    private String deviceId() {
        String id = prefs.getString(DEVICE_ID_KEY, null);
        if (id == null) {
            id = UUID.randomUUID().toString().replace("-", "");
            prefs.edit().putString(DEVICE_ID_KEY, id).apply();
        }
        return id;
    }

    private String deviceLabel() {
        String name = (Build.MANUFACTURER + " " + Build.MODEL).trim();
        return name.length() > 60 ? name.substring(0, 60) : name;
    }

    /** The value of one cookie in a ``Cookie`` header, or ``null``. */
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

    private JSONObject getJson(String path, String cookie, String token) {
        HttpURLConnection connection = null;
        lastStatus = 0;
        try {
            connection = (HttpURLConnection) new URL(BASE_URL + path).openConnection();
            connection.setRequestMethod("GET");
            connection.setConnectTimeout(15_000);
            connection.setReadTimeout(20_000);
            connection.setRequestProperty("Accept", "application/json");
            if (token != null && !token.isEmpty()) {
                connection.setRequestProperty("Authorization", "Device " + token);
            } else if (cookie != null) {
                connection.setRequestProperty("Cookie", cookie);
            }
            connection.setRequestProperty("User-Agent", "TOWEMS-Android/1.0");
            lastStatus = connection.getResponseCode();
            if (lastStatus != 200) {
                Log.i(TAG, path + " answered " + lastStatus);
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

    private PendingIntent openIntent(String path, int requestCode) {
        Intent intent = new Intent(this, MainActivity.class);
        intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        intent.putExtra(MainActivity.EXTRA_PATH, path == null || path.isEmpty() ? "/" : path);
        return PendingIntent.getActivity(
                this,
                requestCode,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private void notifyIfAllowed(int id, Notification notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
                != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            return;
        }
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) {
            manager.notify(id, notification);
        }
    }

    private void cancel(int id) {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) {
            manager.cancel(id);
        }
    }
}
