package online.mrmsystem.towems;

import android.util.Log;

import androidx.annotation.NonNull;

import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;

import java.util.Map;

/**
 * Receives TOWEMS pushes while the app is closed.
 *
 * The watcher's foreground service is a fallback: Android caps long-running
 * services and an OEM battery saver can end one at any time, which is why
 * alerts used to appear only when the app was opened. Push inverts that - Google
 * holds one connection to the phone and starts this service when there is
 * something for the account, the same way WhatsApp and Viber do - so the phone
 * hears about a message with no TOWEMS window anywhere.
 *
 * The server sends data-only messages. This class builds the notification
 * itself, through {@link AlertService#showPushNotification}, so it uses the
 * audible `towems_*_v2` channel, the real unread number for the launcher badge
 * and the deep link into the right screen. Nothing from a conversation is in
 * the payload; a message alert says only that something is waiting.
 */
public class TowemsMessagingService extends FirebaseMessagingService {

    private static final String TAG = "TOWEMS/Push";

    /**
     * A new registration token. Save it, then tell the server from the watcher's
     * own device token: no browser session is needed, and signing out still
     * stops push when that device token is revoked.
     */
    @Override
    public void onNewToken(@NonNull String token) {
        super.onNewToken(token);
        Log.i(TAG, "FCM issued a new registration token");
        AlertService.rememberPushToken(this, token);
        AlertService.registerPushToken(this, true);
    }

    @Override
    public void onMessageReceived(@NonNull RemoteMessage message) {
        Map<String, String> data = message.getData();
        String title = data.get("title");
        String body = data.get("body");
        String path = data.get("path");
        String kind = data.get("kind");
        int count = 0;
        try {
            count = Integer.parseInt(data.get("count") == null ? "0" : data.get("count"));
        } catch (NumberFormatException ignored) {
            // A missing count only means the badge falls back to this banner.
        }
        Log.i(TAG, "push received (" + (kind == null ? "alert" : kind) + ")");
        AlertService.showPushNotification(
                this,
                title == null || title.isEmpty() ? "TOWEMS" : title,
                body == null ? "" : body,
                path,
                count,
                "message".equals(kind),
                "urgent".equals(kind));
        // The token may have been rotated while the app was closed; this is a
        // cheap no-op unless it actually changed.
        AlertService.registerPushToken(this, false);
    }
}
