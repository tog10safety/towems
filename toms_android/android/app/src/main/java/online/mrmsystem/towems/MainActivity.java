package online.mrmsystem.towems;

import android.Manifest;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;

import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

import com.getcapacitor.BridgeActivity;

/**
 * The TOWEMS shell: the web app is the product, and this activity adds the three
 * things a browser tab cannot do - a closed-app alert watcher, a notification
 * permission prompt, and a deep link from a notification into the right screen.
 *
 * It also carries the two consents aggressive OEM battery management makes
 * necessary: a battery-optimisation exemption so Doze does not freeze the
 * watcher, and background location so the driver tracker keeps reporting with
 * the screen off. Each is asked once, behind a sentence explaining why.
 */
public class MainActivity extends BridgeActivity {

    public static final String EXTRA_PATH = "towems_path";
    private static final int NOTIFICATION_PERMISSION_REQUEST = 101;
    private static final int BACKGROUND_LOCATION_REQUEST = 102;

    /** One flag per explanation, so the setup prompts are offered once, not every launch. */
    private static final String SETUP_PREFS = "towems_setup";
    private static final String KEY_BATTERY_ASKED = "battery_prompt_seen";
    private static final String KEY_BACKGROUND_ASKED = "background_location_prompt_seen";

    /** At most one setup dialog per launch, so two never stack on top of each other. */
    private boolean promptShownThisLaunch;
    /** The strip that says whether this phone can actually receive anything. */
    private WatcherBar watcherBar;
    /** Capacitor installs its page view a moment after onCreate on some builds. */
    private int attachAttempts;
    /** Native Keystore-backed biometric bridge exposed to the TOWEMS page. */
    private BiometricBridge biometricBridge;
    private int biometricAttachAttempts;
    /**
     * True while TOWEMS has deliberately handed the screen to another activity:
     * the document picker, the camera, a permission dialog or a settings page.
     * These leaves are temporary, so the decrypted token must stay in memory;
     * a Home/recents leave is not marked and still locks normally.
     */
    private boolean temporaryActivityInProgress;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        attachBiometricBridge();
        attachWatcherBar();
        boolean askingForNotifications = askForNotifications();
        startWatcher();
        TrackingService.startIfPermitted(this);
        openPathFrom(getIntent());
        // A cold start is a deliberate visit, so this is where the setup
        // explanations belong: returning from a settings screen (onResume) must
        // not throw the same dialog at the person who just read it. When the
        // notification dialog is up, the rest waits for its answer instead of
        // stacking a second dialog behind it.
        if (!askingForNotifications) {
            promptShownThisLaunch = false;
            askForReliability();
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        // The permission dialog has closed; the next Home/recents leave is a
        // real background again and must lock.
        temporaryActivityInProgress = false;
        // Capacitor tracks its own permission callbacks through this method.
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode == NOTIFICATION_PERMISSION_REQUEST) {
            promptShownThisLaunch = false;
            if (watcherBar != null) {
                watcherBar.refresh();
            }
            askForReliability();
        }
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
        // Returning from a picker, camera, permission dialog or settings page is
        // not a new visit to the app. A later Home/recents stop is.
        temporaryActivityInProgress = false;
        // The in-app bell already polls while a screen is open; the service must
        // stay quiet then, or every alert arrives twice.
        AlertService.appVisible = true;
        attachBiometricBridge();
        attachWatcherBar();
        startWatcher();
        TrackingService.startIfPermitted(this);
        if (watcherBar != null) {
            watcherBar.refresh();
        }
    }

    @Override
    public void onPause() {
        AlertService.appVisible = false;
        super.onPause();
    }

    @Override
    public void onStop() {
        // Leaving the foreground must not sign the account out: the alert
        // watcher keeps its own credential, and the Keystore refresh token is
        // left on disk. Only the decrypted copy is dropped, so the next open
        // requires the fingerprint or face.
        //
        // A document picker, camera, permission dialog or settings page puts
        // this activity into the background too. Those are temporary detours
        // opened by TOWEMS itself; locking there made choosing a profile photo
        // ask for a fingerprint before the person had even returned to the page
        // they started from. The flag is set before the external activity is
        // launched and cleared when the result/resume comes back, so a genuine
        // Home, recents or screen-off stop still locks.
        if (temporaryActivityInProgress && !isFinishing()) {
            Log.i("TOWEMS/Lifecycle", "temporary activity; keeping the session unlocked");
        } else if (biometricBridge != null) {
            biometricBridge.lockForBackground();
        }
        super.onStop();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        // This also runs before onResume on some Android versions. Clearing it
        // here ensures a later real background cannot inherit the exemption.
        temporaryActivityInProgress = false;
        super.onActivityResult(requestCode, resultCode, data);
    }

    /**
     * ActivityResultLauncher, the file chooser and the camera all end up here.
     * Mark only targets outside TOWEMS: an internal activity is not a reason to
     * keep the app unlocked.
     */
    private void noteTemporaryActivity(Intent intent) {
        if (intent == null) {
            return;
        }
        ComponentName target = intent.getComponent();
        if (target != null && getPackageName().equals(target.getPackageName())) {
            return;
        }
        temporaryActivityInProgress = true;
    }

    /**
     * Request permissions that put a system dialog over TOWEMS. Mark the leave
     * first: on some Android versions the permission controller stops the
     * activity, which would otherwise lock the app while the dialog is still up.
     */
    private void requestTemporaryPermissions(String[] permissions, int requestCode) {
        temporaryActivityInProgress = true;
        ActivityCompat.requestPermissions(this, permissions, requestCode);
    }

    @Override
    @SuppressWarnings("deprecation")
    public void startActivityForResult(Intent intent, int requestCode) {
        noteTemporaryActivity(intent);
        super.startActivityForResult(intent, requestCode);
    }

    @Override
    @SuppressWarnings("deprecation")
    public void startActivityForResult(Intent intent, int requestCode, Bundle options) {
        noteTemporaryActivity(intent);
        super.startActivityForResult(intent, requestCode, options);
    }

    @Override
    public void startActivity(Intent intent) {
        noteTemporaryActivity(intent);
        super.startActivity(intent);
    }

    @Override
    public void startActivity(Intent intent, Bundle options) {
        noteTemporaryActivity(intent);
        super.startActivity(intent, options);
    }

    /**
     * Start the watcher, or ask the running one to poll now.
     *
     * The immediate check matters for the strip: coming back to the app should
     * show a fresh "checked just now" rather than the last round trip from
     * however long ago the phone was in a pocket.
     */
    private void startWatcher() {
        Intent service = new Intent(this, AlertService.class)
                .setAction(AlertService.ACTION_CHECK_NOW);
        ContextCompat.startForegroundService(this, service);
    }

    /**
     * Expose the native biometric bridge once Capacitor has created its WebView.
     * Only the exact TOWEMS origin is accepted by every bridge method.
     */
    private void attachBiometricBridge() {
        if (biometricBridge != null) {
            return;
        }
        if (getBridge() == null || getBridge().getWebView() == null) {
            if (biometricAttachAttempts++ < 30) {
                getWindow().getDecorView().postDelayed(this::attachBiometricBridge, 100);
            }
            return;
        }
        biometricBridge = new BiometricBridge(this);
        getBridge().getWebView().addJavascriptInterface(biometricBridge, "TOWEMSBiometric");
        Log.i("TOWEMS/Biometric", "bridge attached to TOWEMS WebView");
    }

    /**
     * Put the watcher's state above the web app.
     *
     * The bar is a sibling of the page rather than a layer over it, and the page
     * keeps its own insets: the site's header is the first thing a person needs,
     * and a floating banner would cover it exactly when something is wrong.
     */
    private void attachWatcherBar() {
        if (watcherBar != null) {
            return;
        }
        ViewGroup content = findViewById(android.R.id.content);
        if (content == null || content.getChildCount() == 0) {
            // The page view is not in the hierarchy yet on the first frames; a
            // short retry beats a strip that silently never appears.
            if (content != null && attachAttempts++ < 20) {
                content.postDelayed(this::attachWatcherBar, 100);
            }
            return;
        }
        final View host = content.getChildAt(0);
        Log.i("TOWEMS/WatcherBar", "attaching above " + host.getClass().getSimpleName());
        WatcherBar bar = new WatcherBar(this);
        content.addView(bar, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.TOP));
        bar.addOnLayoutChangeListener(
                (view, left, top, right, bottom, oldLeft, oldTop, oldRight, oldBottom) -> {
                    int wanted = bar.getHeight();
                    if (host.getPaddingTop() != wanted) {
                        host.setPadding(host.getPaddingLeft(), wanted,
                                host.getPaddingRight(), host.getPaddingBottom());
                    }
                });
        watcherBar = bar;
    }

    /** Returns true when a permission dialog was just put on screen. */
    private boolean askForNotifications() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
            return false;
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                == PackageManager.PERMISSION_GRANTED) {
            return false;
        }
        requestTemporaryPermissions(
                new String[] { Manifest.permission.POST_NOTIFICATIONS },
                NOTIFICATION_PERMISSION_REQUEST);
        return true;
    }

    /** A notification tapped while the app was closed opens its TOWEMS screen. */
    private void openPathFrom(Intent intent) {
        if (intent == null) {
            return;
        }
        String path = intent.getStringExtra(EXTRA_PATH);
        if (path == null || path.isEmpty()) {
            // A push Play Services drew itself (the app was swiped away) opens
            // this activity with the data payload as extras, not with the
            // watcher's extra. Same destination, different courier.
            path = intent.getStringExtra("path");
        }
        if (path == null || path.isEmpty()) {
            return;
        }
        intent.removeExtra(EXTRA_PATH);
        intent.removeExtra("path");
        final String url = AlertService.BASE_URL + (path.startsWith("/") ? path : "/" + path);
        if (getBridge() == null || getBridge().getWebView() == null) {
            return;
        }
        getBridge().getWebView().post(() -> getBridge().getWebView().loadUrl(url));
    }

    // ---------------------------------------------------------------- setup

    /**
     * Ask for whatever is still missing to survive a phone that naps.
     *
     * Background location only matters once the person has allowed location at
     * all - the driver page asks for that itself the first time it needs a fix -
     * so it is checked first: on a driver's phone it is the more urgent of the
     * two, and it is the one Android 11+ will not reopen by itself.
     */
    private void askForReliability() {
        if (promptShownThisLaunch) {
            return;
        }
        if (askForBackgroundLocation()) {
            return;
        }
        askForBatteryExemption();
    }

    private SharedPreferences setupPrefs() {
        return getSharedPreferences(SETUP_PREFS, MODE_PRIVATE);
    }

    /**
     * "Allow all the time" for location.
     *
     * Android 10 shows its own dialog. Android 11 and later removed the request
     * API for this: the only way in is the app's own settings page, so the
     * button goes there instead of asking for something the system will refuse.
     */
    private boolean askForBackgroundLocation() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            return false;
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
                != PackageManager.PERMISSION_GRANTED) {
            return false;
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_BACKGROUND_LOCATION)
                == PackageManager.PERMISSION_GRANTED) {
            return false;
        }
        SharedPreferences setup = setupPrefs();
        if (setup.getBoolean(KEY_BACKGROUND_ASKED, false)) {
            return false;
        }

        promptShownThisLaunch = true;
        new AlertDialog.Builder(this)
                .setTitle(R.string.setup_location_title)
                .setMessage(R.string.setup_location_message)
                .setPositiveButton(R.string.setup_allow, (dialog, which) -> {
                    // Deliberately not recorded as asked: Android 11+ cannot grant
                    // this from a dialog, only from the settings page, and a person
                    // who backs out of there has not decided anything yet.
                    if (Build.VERSION.SDK_INT == Build.VERSION_CODES.Q) {
                        requestTemporaryPermissions(
                                new String[] { Manifest.permission.ACCESS_BACKGROUND_LOCATION },
                                BACKGROUND_LOCATION_REQUEST);
                    } else {
                        openAppSettings();
                    }
                })
                .setNegativeButton(R.string.setup_later, (dialog, which) ->
                        setup.edit().putBoolean(KEY_BACKGROUND_ASKED, true).apply())
                .show();
        return true;
    }

    /**
     * The battery-optimisation exemption.
     *
     * Without it Doze suspends the watcher's poll and the phone stops hearing
     * about abort orders until someone opens the app - exactly the case the
     * watcher exists for. "App info" is offered for the OEMs that hide their own
     * Autostart switch, which no request API can reach.
     */
    private boolean askForBatteryExemption() {
        PowerManager power = (PowerManager) getSystemService(Context.POWER_SERVICE);
        if (power == null || power.isIgnoringBatteryOptimizations(getPackageName())) {
            return false;
        }
        SharedPreferences setup = setupPrefs();
        if (setup.getBoolean(KEY_BATTERY_ASKED, false)) {
            return false;
        }

        promptShownThisLaunch = true;
        new AlertDialog.Builder(this)
                .setTitle(R.string.setup_battery_title)
                .setMessage(R.string.setup_battery_message)
                .setPositiveButton(R.string.setup_allow, (dialog, which) -> {
                    setup.edit().putBoolean(KEY_BATTERY_ASKED, true).apply();
                    Intent request = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS);
                    request.setData(Uri.parse("package:" + getPackageName()));
                    startSettings(request);
                })
                .setNeutralButton(R.string.setup_app_info, (dialog, which) -> {
                    setup.edit().putBoolean(KEY_BATTERY_ASKED, true).apply();
                    openAppSettings();
                })
                .setNegativeButton(R.string.setup_later, (dialog, which) ->
                        setup.edit().putBoolean(KEY_BATTERY_ASKED, true).apply())
                .show();
        return true;
    }

    private void openAppSettings() {
        startSettings(new Intent(
                Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.parse("package:" + getPackageName())));
    }

    /** A settings screen a stripped ROM may not have; app info always exists. */
    private void startSettings(Intent intent) {
        try {
            startActivity(intent);
        } catch (ActivityNotFoundException missing) {
            openAppSettings();
        }
    }
}
