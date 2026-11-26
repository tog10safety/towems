package online.mrmsystem.towems;

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;

import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

import com.getcapacitor.BridgeActivity;

/**
 * The TOWEMS shell: the web app is the product, and this activity adds the three
 * things a browser tab cannot do - a closed-app alert watcher, a notification
 * permission prompt, and a deep link from a notification into the right screen.
 */
public class MainActivity extends BridgeActivity {

    public static final String EXTRA_PATH = "towems_path";
    private static final int NOTIFICATION_PERMISSION_REQUEST = 101;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        askForNotifications();
        startWatcher();
        openPathFrom(getIntent());
    }

    @Override
    public void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        openPathFrom(intent);
    }

    @Override
    public void onResume() {
        super.onResume();
        // The in-app bell already polls while a screen is open; the service must
        // stay quiet then, or every alert arrives twice.
        AlertService.appVisible = true;
        startWatcher();
    }

    @Override
    public void onPause() {
        AlertService.appVisible = false;
        super.onPause();
    }

    private void startWatcher() {
        Intent service = new Intent(this, AlertService.class);
        ContextCompat.startForegroundService(this, service);
    }

    private void askForNotifications() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
            return;
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                == PackageManager.PERMISSION_GRANTED) {
            return;
        }
        ActivityCompat.requestPermissions(
                this,
                new String[] { Manifest.permission.POST_NOTIFICATIONS },
                NOTIFICATION_PERMISSION_REQUEST);
    }

    /** A notification tapped while the app was closed opens its TOWEMS screen. */
    private void openPathFrom(Intent intent) {
        if (intent == null) {
            return;
        }
        String path = intent.getStringExtra(EXTRA_PATH);
        if (path == null || path.isEmpty()) {
            return;
        }
        intent.removeExtra(EXTRA_PATH);
        final String url = AlertService.BASE_URL + (path.startsWith("/") ? path : "/" + path);
        if (getBridge() == null || getBridge().getWebView() == null) {
            return;
        }
        getBridge().getWebView().post(() -> getBridge().getWebView().loadUrl(url));
    }
}
