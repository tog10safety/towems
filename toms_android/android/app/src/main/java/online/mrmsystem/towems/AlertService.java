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
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;

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

    /** Shared with the in-app strip and the driver tracker. */
    static final String PREFS_NAME = "towems_alerts";
    static final String TOKEN_KEY = "device_token";
    /** The session the stored token was minted from. */
    static final String TOKEN_SESSION_KEY = "token_session";
    private static final String DEVICE_ID_KEY = "device_id";

    /** What the last round trip did, written for the strip to read. */
    static final String KEY_LAST_CHECK = "last_check_ms";
    static final String KEY_LAST_OK = "last_ok_ms";
    static final String KEY_LAST_HTTP = "last_http";
    static final String KEY_LAST_ERROR = "last_error";
    /** A tap on "Check now": one immediate poll, then the usual rhythm. */
    public static final String ACTION_CHECK_NOW = "online.mrmsystem.towems.action.CHECK_NOW";

    private static final long POLL_MS = 20_000L;
    private static final long SIGNED_OUT_MS = 300_000L;

    // "_v2" channels: a channel's importance and vibration are fixed once it
    // exists, and the first release created these without vibration. New ids are
    // the only way an installed phone picks up the louder settings.
    private static final String CHANNEL_ALERTS = "towems_alerts_v2";
    static final String CHANNEL_MESSAGES = "towems_messages_v2";
    private static final String CHANNEL_SERVICE = "towems_service";
    private static final String CHANNEL_ALERTS_V1 = "towems_alerts";
    private static final String CHANNEL_MESSAGES_V1 = "towems_messages";

    /** A double buzz: long enough to feel through a pocket, short enough to read. */
    private static final long[] VIBRATION = { 0, 450, 200, 450 };

    private static final int SERVICE_NOTIFICATION_ID = 1;
    private static final int MESSAGES_NOTIFICATION_ID = 1002;
    private static final int INBOX_NOTIFICATION_ID = 1001;
    private static final int TEST_NOTIFICATION_ID = 1003;
    private static final int SUMMARY_NOTIFICATION_ID = 1004;

    /** MainActivity keeps this true while a TOWEMS screen is visible. */
    public static volatile boolean appVisible = false;

    /** True while one watcher instance is alive; the in-app strip reads it. */
    private static volatile boolean running;

    private static final List<StateListener> listeners = new CopyOnWriteArrayList<>();

    /** The in-app strip redraws whenever one of these fires. */
    public interface StateListener {
        void onWatcherState();
    }

    public static boolean isRunning() {
        return running;
    }

    public static void watch(StateListener listener) {
        if (listener != null && !listeners.contains(listener)) {
            listeners.add(listener);
        }
    }

    public static void unwatch(StateListener listener) {
        listeners.remove(listener);
    }

    private static void publish() {
        for (StateListener listener : listeners) {
            listener.onWatcherState();
        }
    }

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
            // Stamped before the call, not after: a request that hangs still
            // proves the watcher itself is alive.
            prefs.edit().putLong(KEY_LAST_CHECK, System.currentTimeMillis()).apply();
            try {
                next = pollOnce();
            } catch (Exception error) {
                Log.w(TAG, "poll failed", error);
                recordFailure(0, error.getClass().getSimpleName());
            }
            publish();
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
        prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        ensureChannels(this);
        startWatching();
        running = true;
        publish();
        handler.post(tick);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // "Check now" from the in-app strip: one immediate round trip, then the
        // normal twenty-second rhythm resumes. Removing the pending tick first
        // keeps a single poll loop rather than a growing pile of them.
        if (intent != null && ACTION_CHECK_NOW.equals(intent.getAction()) && handler != null) {
            handler.removeCallbacks(tick);
            handler.post(tick);
        }
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
        running = false;
        publish();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    /** Channels exist for as long as the install does; the test path needs them too. */
    private static void ensureChannels(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager manager =
                (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager == null) {
            return;
        }
        NotificationChannel alerts = new NotificationChannel(
                CHANNEL_ALERTS, "Operational alerts", NotificationManager.IMPORTANCE_HIGH);
        alerts.setDescription("Abort orders, emergencies, movement tasks and approvals.");
        alerts.enableVibration(true);
        alerts.setVibrationPattern(VIBRATION);
        // A channel that allows badges is half of what a launcher needs; the
        // other half is Notification.number on each alert (see showAlert).
        alerts.setShowBadge(true);
        manager.createNotificationChannel(alerts);

        NotificationChannel messages = new NotificationChannel(
                CHANNEL_MESSAGES, "Messages", NotificationManager.IMPORTANCE_HIGH);
        messages.setDescription("A message is waiting. The text stays in TOWEMS.");
        messages.enableVibration(true);
        messages.setVibrationPattern(VIBRATION);
        // One line, on a locked screen, without the conversation itself.
        messages.setLockscreenVisibility(Notification.VISIBILITY_PRIVATE);
        messages.setShowBadge(true);
        manager.createNotificationChannel(messages);

        // The first release's silent channels, removed so Settings shows one
        // alert channel and one message channel rather than four.
        manager.deleteNotificationChannel(CHANNEL_ALERTS_V1);
        manager.deleteNotificationChannel(CHANNEL_MESSAGES_V1);

        NotificationChannel service = new NotificationChannel(
                CHANNEL_SERVICE, "Alert watcher", NotificationManager.IMPORTANCE_LOW);
        service.setDescription("Keeps TOWEMS able to reach this phone.");
        service.setShowBadge(false);
        manager.createNotificationChannel(service);
    }

    private void startWatching() {
        Notification notification = new NotificationCompat.Builder(this, CHANNEL_SERVICE)
                .setSmallIcon(R.drawable.ic_stat_towems)
                .setContentTitle("TOWEMS is watching for alerts")
                .setContentText("Notifications arrive while the app is closed.")
                .setOngoing(true)
                .setSilent(true)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .setContentIntent(openIntent(this, "/notifications", 0))
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
            recordFailure(0, null);
            return SIGNED_OUT_MS;
        }

        long since = prefs.getLong("latest_id", 0);
        JSONObject pulse = getJson("/api/notifications/pulse/?since=" + since, cookie, token);
        if (pulse == null && (lastStatus == 401 || lastStatus == 403)) {
            // The token was revoked (sign-out, password change) or expired.
            forgetToken();
            token = session == null ? null : mint(cookie, session);
            if (token == null) {
                recordFailure(0, null);
                return SIGNED_OUT_MS;
            }
            pulse = getJson("/api/notifications/pulse/?since=" + since, cookie, token);
        }
        if (pulse == null) {
            // No reply at all is a network blip: retry on the normal interval.
            recordFailure(lastStatus, lastStatus == 0 ? "no reply" : "HTTP " + lastStatus);
            return lastStatus == 0 ? POLL_MS : SIGNED_OUT_MS;
        }
        recordSuccess();

        boolean firstRun = !prefs.contains("latest_id");
        long latestId = pulse.optLong("latest_id", 0);
        // ``items`` is every new unread row, whatever its priority; ``urgent``
        // is the older, narrower list kept for servers that predate it.
        JSONArray urgent = pulse.optJSONArray("items");
        if (urgent == null) {
            urgent = pulse.optJSONArray("urgent");
        }
        // The launcher's number is the account's real unread count, not the
        // number of banners still in the shade; the server computes it, so it
        // also falls back to zero once everything has been read.
        int unread = pulse.has("unread")
                ? pulse.optInt("unread", 0)
                : (urgent == null ? 0 : urgent.length());
        showUnreadSummary(unread);
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
                showAlert(row, unread);
            }
        }
        // The first poll only establishes a baseline: opening the app for the
        // first time must not replay yesterday's inbox.
        rememberSeen(urgent);
        prefs.edit().putLong("latest_id", latestId).apply();

        int pending = pulse.optInt("pending_actions", 0);
        showPendingActions(pending);

        // A server that predates ``items`` still has to make the phone buzz for
        // a message: the unread count is the only thing it offers there.
        if (!pulse.has("items")) {
            pollMessages(cookie, token);
        }
        return POLL_MS;
    }

    /**
     * The older, count-only message alert.
     *
     * It says that something is waiting, never who wrote or what was said, and
     * it is used only where the pulse cannot name the conversation itself.
     */
    private void pollMessages(String cookie, String token) {
        JSONObject summary = getJson("/api/messaging/summary/", cookie, token);
        if (summary == null) {
            return;
        }
        int unread = summary.optInt("unread_total", 0);
        showUnreadSummary(unread);
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
                .setSmallIcon(R.drawable.ic_stat_towems)
                .setContentTitle(unread == 1 ? "New message" : "New messages")
                .setContentText("Open TOWEMS to read it.")
                .setAutoCancel(true)
                .setOnlyAlertOnce(true)
                .setNumber(unread)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setCategory(NotificationCompat.CATEGORY_MESSAGE)
                .setVibrate(VIBRATION)
                .setContentIntent(openIntent(this, "/messages", 1))
                .build();
        notifyIfAllowed(MESSAGES_NOTIFICATION_ID, notification);
    }

    /**
     * One notification per new row, on the channel its kind deserves.
     *
     * A direct message, a group chat, an instruction and an abort order all
     * arrive here now; the server already decided that the body of a chat alert
     * is "Open Messages to read it.", so nothing from the conversation itself
     * ever reaches the lock screen.
     */
    private void showAlert(JSONObject row, int unread) {
        String title = row.optString("title", "TOWEMS");
        String message = row.optString("message", "");
        String path = row.optString("action_url", "/notifications");
        boolean urgent = "urgent".equals(row.optString("priority", ""))
                || "high".equals(row.optString("priority", ""));
        boolean messageRow = "message".equals(row.optString("category", ""));
        String channel = messageRow ? CHANNEL_MESSAGES : CHANNEL_ALERTS;
        NotificationCompat.Builder builder = new NotificationCompat.Builder(this, channel)
                .setSmallIcon(R.drawable.ic_stat_towems)
                .setContentTitle(title.length() > 120 ? title.substring(0, 120) : title)
                .setStyle(new NotificationCompat.BigTextStyle().bigText(message))
                .setNumber(unread)
                .setAutoCancel(true)
                .setPriority(urgent ? NotificationCompat.PRIORITY_MAX : NotificationCompat.PRIORITY_HIGH)
                .setCategory(messageRow
                        ? NotificationCompat.CATEGORY_MESSAGE
                        : urgent ? NotificationCompat.CATEGORY_ALARM : NotificationCompat.CATEGORY_EVENT)
                .setVibrate(VIBRATION)
                .setContentIntent(openIntent(this, path, alertNotificationId(row.optLong("id", 0))));
        notifyIfAllowed(alertNotificationId(row.optLong("id", 0)), builder.build());
    }

    /** One id per notification row, stable across polls. */
    private static int alertNotificationId(long id) {
        return (int) (id % 100000) + 10000;
    }

    /**
     * The number on the launcher icon, as one standing notification.
     *
     * A launcher draws the badge from what is in the shade, so per-row banners
     * alone kept the icon lit after everything had been read in the app: the
     * banners stayed until they were dismissed by hand. This line carries the
     * account's real unread count instead, and is rewritten on every poll - a
     * no-op update while the count is steady, silence and no vibration because
     * the count is not itself news - and removed at zero, along with the banners
     * that are already read. Reading in the app therefore clears the icon on the
     * next poll rather than whenever the phone is tidied up.
     */
    private void showUnreadSummary(int unread) {
        if (unread <= 0) {
            cancel(SUMMARY_NOTIFICATION_ID);
            // Everything is read, so the banners in the shade are stale: drop
            // them too, or a launcher that counts notifications keeps the badge.
            for (Long id : seen) {
                cancel(alertNotificationId(id));
            }
            return;
        }
        Notification notification = new NotificationCompat.Builder(this, CHANNEL_ALERTS)
                .setSmallIcon(R.drawable.ic_stat_towems)
                .setContentTitle(unread == 1 ? "1 unread item" : unread + " unread items")
                .setContentText("Open TOWEMS to read them.")
                // The number the launcher may draw on the icon.
                .setNumber(unread)
                .setAutoCancel(true)
                .setOnlyAlertOnce(true)
                .setSilent(true)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .setCategory(NotificationCompat.CATEGORY_STATUS)
                .setContentIntent(openIntent(this, "/notifications", 3))
                .build();
        notifyIfAllowed(SUMMARY_NOTIFICATION_ID, notification);
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
                .setSmallIcon(R.drawable.ic_stat_towems)
                .setContentTitle(pending == 1 ? "1 action is waiting" : pending + " actions are waiting")
                .setContentText("Approvals and movement tasks you still need to do.")
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setContentIntent(openIntent(this, "/notifications?scope=action", 2))
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
                prefs.edit()
                        .putString(KEY_LAST_ERROR, "sign-in exchange answered " + code)
                        .apply();
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

    private static PendingIntent openIntent(Context context, String path, int requestCode) {
        Intent intent = new Intent(context, MainActivity.class);
        intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        intent.putExtra(MainActivity.EXTRA_PATH, path == null || path.isEmpty() ? "/" : path);
        return PendingIntent.getActivity(
                context,
                requestCode,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private void notifyIfAllowed(int id, Notification notification) {
        if (!canPostNotifications(this)) {
            // The old build returned here in silence, which is why a phone with
            // notifications denied looked exactly like a quiet account.
            Log.w(TAG, "notification " + id + " dropped: notifications are blocked");
            return;
        }
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) {
            try {
                manager.notify(id, notification);
            } catch (RuntimeException refused) {
                Log.w(TAG, "notification " + id + " refused", refused);
            }
        }
    }

    /** Whether Android will actually draw a notification right now. */
    public static boolean canPostNotifications(Context context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
                != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            return false;
        }
        NotificationManager manager =
                (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        return manager == null || manager.areNotificationsEnabled();
    }

    /**
     * One notification through the same channel, sound and vibration a real
     * message uses, so a person can prove this phone will show what it receives.
     *
     * It is local on purpose: a server round trip would only re-test what the
     * strip already reports, while this exercises the part Android can block.
     * Returns false when notifications are blocked, where nothing can be proven.
     */
    public static boolean sendTestNotification(Context context) {
        ensureChannels(context);
        if (!canPostNotifications(context)) {
            Log.w(TAG, "test notification not sent: notifications are blocked");
            return false;
        }
        String text = context.getString(R.string.watcher_test_text);
        Notification notification = new NotificationCompat.Builder(context, CHANNEL_MESSAGES)
                .setSmallIcon(R.drawable.ic_stat_towems)
                .setContentTitle(context.getString(R.string.watcher_test_title))
                .setContentText(text)
                .setStyle(new NotificationCompat.BigTextStyle().bigText(text))
                .setAutoCancel(true)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setCategory(NotificationCompat.CATEGORY_MESSAGE)
                .setVibrate(VIBRATION)
                .setContentIntent(openIntent(context, "/notifications", TEST_NOTIFICATION_ID))
                .build();
        NotificationManager manager =
                (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager == null) {
            return false;
        }
        try {
            manager.notify(TEST_NOTIFICATION_ID, notification);
            return true;
        } catch (RuntimeException refused) {
            Log.w(TAG, "test notification refused", refused);
            return false;
        }
    }

    private void recordSuccess() {
        prefs.edit()
                .putLong(KEY_LAST_OK, System.currentTimeMillis())
                .putInt(KEY_LAST_HTTP, 200)
                .remove(KEY_LAST_ERROR)
                .apply();
    }

    private void recordFailure(int status, String detail) {
        SharedPreferences.Editor edit = prefs.edit().putInt(KEY_LAST_HTTP, status);
        if (detail != null) {
            edit.putString(KEY_LAST_ERROR, detail);
        }
        edit.apply();
    }

    private void cancel(int id) {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) {
            manager.cancel(id);
        }
    }
}
