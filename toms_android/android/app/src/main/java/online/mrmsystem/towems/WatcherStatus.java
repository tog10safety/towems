package online.mrmsystem.towems;

import android.Manifest;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.PowerManager;

/**
 * What this phone can prove about the watcher at this moment.
 *
 * A person who installs the app and hears nothing has no way to tell the
 * difference between a TOWEMS with no news, a watcher that was silenced by
 * Android, and a watcher that never got its alert key. This snapshot answers
 * that question from things the phone already knows - the notification
 * permission, the channel's own importance, the token in the watcher's
 * preferences and the last server round trip the service recorded - so the
 * strip over the web app can say which of them it is.
 *
 * It reads; it never writes and never asks the server for anything.
 */
public final class WatcherStatus {

    /**
     * Ordered by what a person has to fix first: a blocked notification cannot
     * be worked around, a stopped watcher cannot be persuaded to poll, and the
     * rest are states that heal themselves once the server answers.
     */
    public enum Level { LIVE, STARTING, SIGNING_IN, OFFLINE, NO_TOKEN, STOPPED, BLOCKED }

    /** Six missed polls: the service asks the server once every twenty seconds. */
    private static final long STALE_MS = 120_000L;

    public final Level level;
    public final boolean notificationsBlocked;
    public final boolean channelSilenced;
    public final boolean serviceRunning;
    public final boolean tokenPresent;
    public final boolean sessionPresent;
    public final long lastCheckMs;
    public final long lastOkMs;
    public final int lastHttp;
    public final String lastError;
    public final boolean batteryExempt;

    private WatcherStatus(Level level, boolean notificationsBlocked, boolean channelSilenced,
            boolean serviceRunning, boolean tokenPresent, boolean sessionPresent,
            long lastCheckMs, long lastOkMs, int lastHttp, String lastError,
            boolean batteryExempt) {
        this.level = level;
        this.notificationsBlocked = notificationsBlocked;
        this.channelSilenced = channelSilenced;
        this.serviceRunning = serviceRunning;
        this.tokenPresent = tokenPresent;
        this.sessionPresent = sessionPresent;
        this.lastCheckMs = lastCheckMs;
        this.lastOkMs = lastOkMs;
        this.lastHttp = lastHttp;
        this.lastError = lastError;
        this.batteryExempt = batteryExempt;
    }

    public static WatcherStatus read(Context context) {
        SharedPreferences prefs = context.getSharedPreferences(
                AlertService.PREFS_NAME, Context.MODE_PRIVATE);

        boolean permission = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU
                || context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                == PackageManager.PERMISSION_GRANTED;
        NotificationManager manager =
                (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        boolean enabled = manager == null || manager.areNotificationsEnabled();
        boolean silenced = false;
        if (manager != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            // A channel's importance is frozen once created, and a person can
            // lower it in Settings; the messages channel is the one every
            // conversation and notice lands on.
            NotificationChannel channel =
                    manager.getNotificationChannel(AlertService.CHANNEL_MESSAGES);
            // Not just "off": a channel the person (or a battery tool) turned
            // quiet still draws the banner but never makes a sound, which looks
            // exactly like a broken watcher from the other side of the phone.
            silenced = channel != null
                    && (channel.getImportance() < NotificationManager.IMPORTANCE_DEFAULT
                            || channel.getSound() == null);
        }
        boolean blocked = !permission || !enabled || silenced;

        boolean tokenPresent = !isEmpty(prefs.getString(AlertService.TOKEN_KEY, null));
        boolean sessionPresent = !isEmpty(prefs.getString(AlertService.TOKEN_SESSION_KEY, null));

        long lastCheckMs = prefs.getLong(AlertService.KEY_LAST_CHECK, 0);
        long lastOkMs = prefs.getLong(AlertService.KEY_LAST_OK, 0);
        int lastHttp = prefs.getInt(AlertService.KEY_LAST_HTTP, 0);
        String lastError = prefs.getString(AlertService.KEY_LAST_ERROR, null);

        PowerManager power = (PowerManager) context.getSystemService(Context.POWER_SERVICE);
        boolean exempt = power != null
                && power.isIgnoringBatteryOptimizations(context.getPackageName());

        boolean running = AlertService.isRunning();
        Level level;
        if (blocked) {
            level = Level.BLOCKED;
        } else if (!running) {
            level = Level.STOPPED;
        } else if (!tokenPresent && !sessionPresent) {
            level = Level.NO_TOKEN;
        } else if (!tokenPresent) {
            level = Level.SIGNING_IN;
        } else if (lastOkMs == 0) {
            level = Level.STARTING;
        } else if (lastHttp != 200
                || System.currentTimeMillis() - lastOkMs > STALE_MS) {
            level = Level.OFFLINE;
        } else {
            level = Level.LIVE;
        }

        return new WatcherStatus(level, blocked, silenced, running, tokenPresent,
                sessionPresent, lastCheckMs, lastOkMs, lastHttp, lastError, exempt);
    }

    public boolean notificationsAllowed() {
        return !notificationsBlocked;
    }

    private static boolean isEmpty(String value) {
        return value == null || value.isEmpty();
    }
}
