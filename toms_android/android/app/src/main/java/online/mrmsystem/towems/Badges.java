package online.mrmsystem.towems;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

/**
 * The unread count on the launcher icon.
 *
 * A launcher draws that number itself; an app can only ask. Stock Android shows
 * a dot for any notification whose channel allows badges, while the vendors that
 * draw a number read it from their own broadcast - or, where the launcher counts
 * notifications, from how many TOWEMS left in the shade. So the watcher does all
 * three: it reports the server's unread count here, marks the channels badging,
 * and stamps the count on the notifications it posts.
 *
 * Sending is best effort: a launcher that listens gets the number, and one that
 * does not is unaffected. Nothing here needs a permission, and a badge can never
 * be the only sign that something arrived.
 */
final class Badges {

    private static final String TAG = "TOWEMS/Badges";

    /** The last number sent, so a 20-second poll does not repeat itself. */
    private static int lastSent = -1;

    private Badges() {
    }

    static void set(Context context, int count) {
        int badge = Math.max(0, count);
        if (badge == lastSent) {
            return;
        }
        lastSent = badge;

        ComponentName launcher = new ComponentName(context, MainActivity.class);
        String packageName = launcher.getPackageName();
        String className = launcher.getClassName();

        // Samsung's TouchWiz/One UI launcher.
        Intent samsung = new Intent("android.intent.action.BADGE_COUNT_UPDATE");
        samsung.putExtra("badge_count_package_name", packageName);
        samsung.putExtra("badge_count_class_name", className);
        samsung.putExtra("badge_count", badge);
        send(context, samsung);

        // Sony's Xperia launcher.
        Intent sony = new Intent("com.sonyericsson.home.action.UPDATE_BADGE");
        sony.putExtra("com.sonyericsson.home.intent.extra.badge.PACKAGE_NAME", packageName);
        sony.putExtra("com.sonyericsson.home.intent.extra.badge.ACTIVITY_NAME", className);
        sony.putExtra("com.sonyericsson.home.intent.extra.badge.SHOW_MESSAGE", badge > 0);
        sony.putExtra("com.sonyericsson.home.intent.extra.badge.MESSAGE", String.valueOf(badge));
        sony.putExtra("com.sonyericsson.home.intent.extra.badge.COUNT", badge);
        send(context, sony);

        // HTC's Sense launcher.
        Intent htc = new Intent("com.htc.launcher.action.UPDATE_SHORTCUT");
        htc.putExtra("packagename", packageName);
        htc.putExtra("count", badge);
        send(context, htc);

        Log.i(TAG, "launcher badge " + badge);
    }

    private static void send(Context context, Intent intent) {
        // An explicit broadcast to a vendor launcher that may be stopped.
        intent.addFlags(Intent.FLAG_INCLUDE_STOPPED_PACKAGES);
        try {
            context.sendBroadcast(intent);
        } catch (RuntimeException refused) {
            Log.i(TAG, "badge broadcast refused: " + refused.getClass().getSimpleName());
        }
    }
}
