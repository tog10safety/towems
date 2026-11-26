package online.mrmsystem.towems;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

import androidx.core.content.ContextCompat;

/**
 * Restarts the alert watcher after a phone reboot.
 *
 * It does not check the session first: the WebView's cookie store is not ready
 * this early, and the service is harmless when signed out - it simply waits for
 * the next sign-in instead of reaching the server.
 */
public class BootReceiver extends BroadcastReceiver {

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) {
            return;
        }
        try {
            ContextCompat.startForegroundService(context, new Intent(context, AlertService.class));
        } catch (RuntimeException refused) {
            // Android 15 refuses a boot-started data-sync service on some builds.
            // The watcher starts again on the next launch; nothing is lost.
        }
    }
}
