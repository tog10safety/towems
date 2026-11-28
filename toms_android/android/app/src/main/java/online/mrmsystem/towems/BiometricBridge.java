package online.mrmsystem.towems;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import android.util.Log;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;

import androidx.biometric.BiometricManager;
import androidx.biometric.BiometricPrompt;
import androidx.core.content.ContextCompat;
import com.getcapacitor.BridgeActivity;

import org.json.JSONObject;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * Native biometric gate and Keystore wrapper for the rotating refresh token.
 *
 * The WebView only ever receives a 15-minute access token. The refresh token is
 * encrypted with an AES key held by Android Keystore and is written to private
 * SharedPreferences; neither the password nor any biometric data is stored or
 * exposed to JavaScript. A page from another origin is refused by {@link #trusted()}.
 */
public class BiometricBridge {

    private static final String TAG = "TOWEMS/Biometric";
    private static final String BASE_URL = AlertService.BASE_URL;
    private static final String PREFS = "towems_biometric";
    private static final String KEY_ALIAS = "towems_biometric_refresh";
    private static final String KEY_CIPHERTEXT = "refresh_ciphertext";
    private static final String KEY_IV = "refresh_iv";
    private static final String KEY_USERNAME = "refresh_username";
    private static final String KEY_DEVICE_ID = "device_id";
    private static final String KEY_AUTO_LOCK = "auto_lock";
    private static final int GCM_TAG_BITS = 128;

    private final BridgeActivity activity;
    private final SharedPreferences prefs;

    /** Decrypted refresh token, in process memory only while the shell is unlocked. */
    private volatile String unlockedRefresh;
    private volatile String unlockedUsername;
    /** True after the activity is closed/backgrounded until a successful unlock. */
    private volatile boolean locked = true;

    public BiometricBridge(BridgeActivity activity) {
        this.activity = activity;
        this.prefs = activity.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private boolean trusted() {
        try {
            if (activity.getBridge() == null || activity.getBridge().getWebView() == null) {
                return false;
            }
            final WebView webView = activity.getBridge().getWebView();
            // ``addJavascriptInterface`` invokes this on a WebView background
            // thread. Read the URL on the UI thread as WebView requires, or the
            // call can fail and every bridge method is rejected as untrusted.
            final String[] currentUrl = new String[1];
            final CountDownLatch ready = new CountDownLatch(1);
            activity.runOnUiThread(() -> {
                try {
                    currentUrl[0] = webView.getUrl();
                } finally {
                    ready.countDown();
                }
            });
            if (!ready.await(2, TimeUnit.SECONDS)) {
                return false;
            }
            String url = currentUrl[0];
            if (url == null) {
                return false;
            }
            Uri parsed = Uri.parse(url);
            // Compare the host, not a string prefix: mrm-system.online.evil
            // must never be treated as the TOWEMS origin.
            return "https".equals(parsed.getScheme())
                    && "mrm-system.online".equals(parsed.getHost());
        } catch (RuntimeException | InterruptedException error) {
            if (error instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            return false;
        }
    }

    private String error(String reason, String message) {
        JSONObject result = new JSONObject();
        try {
            result.put("ok", false);
            result.put("reason", reason);
            result.put("message", message);
        } catch (Exception ignored) {
            // JSONObject on Android does not throw for these primitive puts.
        }
        return result.toString();
    }

    private String success(String accessToken, int expiresIn) {
        JSONObject result = new JSONObject();
        try {
            result.put("ok", true);
            result.put("access_token", accessToken);
            result.put("expires_in", expiresIn);
        } catch (Exception ignored) {
            // no-op
        }
        return result.toString();
    }

    @JavascriptInterface
    public boolean isAvailable() {
        if (!trusted()) {
            return false;
        }
        try {
            BiometricManager manager = BiometricManager.from(activity);
            return manager.canAuthenticate(BiometricManager.Authenticators.BIOMETRIC_STRONG)
                    == BiometricManager.BIOMETRIC_SUCCESS;
        } catch (RuntimeException error) {
            Log.w(TAG, "biometric availability check failed", error);
            return false;
        }
    }

    @JavascriptInterface
    public boolean hasToken() {
        if (!trusted()) {
            return false;
        }
        return prefs.contains(KEY_CIPHERTEXT) && prefs.contains(KEY_IV);
    }

    @JavascriptInterface
    public String getUsername() {
        if (!trusted()) {
            return "";
        }
        return prefs.getString(KEY_USERNAME, "");
    }

    @JavascriptInterface
    public String getDeviceId() {
        if (!trusted()) {
            return "";
        }
        return deviceId();
    }

    /** Whether the app currently needs a biometric prompt before showing data. */
    @JavascriptInterface
    public boolean isLocked() {
        if (!trusted()) {
            return true;
        }
        return locked;
    }

    /** Settings preference: lock the decrypted token when the app leaves the foreground. */
    @JavascriptInterface
    public boolean isAutoLockEnabled() {
        if (!trusted()) {
            return false;
        }
        return autoLockPreference();
    }

    @JavascriptInterface
    public boolean setAutoLockEnabled(boolean enabled) {
        if (!trusted()) {
            return false;
        }
        prefs.edit().putBoolean(KEY_AUTO_LOCK, enabled).apply();
        return true;
    }

    /**
     * A completed password + MFA sign-in is at least as strong as a biometric
     * unlock. Clear the locked flag for this process without reading the token.
     */
    @JavascriptInterface
    public boolean markAuthenticated() {
        if (!trusted()) {
            return false;
        }
        if (!hasStoredToken()) {
            return false;
        }
        locked = false;
        return true;
    }

    private boolean autoLockPreference() {
        return prefs.getBoolean(KEY_AUTO_LOCK, true);
    }

    /**
     * Called by the activity when it stops. This is not a sign-out: the
     * Keystore token stays on disk and the server session is untouched, so
     * notification delivery and the next biometric unlock both continue.
     */
    public boolean lockForBackground() {
        if (!autoLockPreference()) {
            return false;
        }
        // No synchronized block here: onStop can arrive while unlock() is
        // waiting for the system prompt, and blocking the UI thread would stop
        // that prompt from ever completing.
        unlockedRefresh = null;
        unlockedUsername = null;
        locked = true;
        return hasStoredToken();
    }

    /**
     * Save a freshly enrolled refresh token. Called only after password + MFA in
     * the WebView; the exchange happens against the live Django session.
     */
    @JavascriptInterface
    public boolean storeRefreshToken(String deviceId, String username, String refreshToken) {
        if (!trusted() || refreshToken == null || refreshToken.isEmpty()) {
            return false;
        }
        if (deviceId == null || !deviceId.equals(deviceId())) {
            return false;
        }
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, secretKey());
            byte[] encrypted = cipher.doFinal(refreshToken.getBytes(StandardCharsets.UTF_8));
            prefs.edit()
                    .putString(KEY_CIPHERTEXT, Base64.encodeToString(encrypted, Base64.NO_WRAP))
                    .putString(KEY_IV, Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP))
                    .putString(KEY_USERNAME, username == null ? "" : username)
                    .apply();
            unlockedRefresh = refreshToken;
            unlockedUsername = username == null ? "" : username;
            locked = false;
            return true;
        } catch (Exception error) {
            Log.w(TAG, "could not store refresh token", error);
            return false;
        }
    }

    @JavascriptInterface
    public void clearToken() {
        if (!trusted()) {
            return;
        }
        unlockedRefresh = null;
        unlockedUsername = null;
        locked = true;
        prefs.edit()
                .remove(KEY_CIPHERTEXT)
                .remove(KEY_IV)
                .remove(KEY_USERNAME)
                .apply();
    }

    /**
     * Forget the decrypted token for this process without deleting the
     * Keystore-protected copy. The next unlock therefore has to ask for the
     * fingerprint or face again, which is what the Settings test action uses.
     */
    @JavascriptInterface
    public boolean lock() {
        if (!trusted()) {
            return false;
        }
        synchronized (this) {
            unlockedRefresh = null;
            unlockedUsername = null;
            locked = true;
        }
        return hasStoredToken();
    }

    /**
     * Prompt for fingerprint/face and then rotate the stored refresh token.
     * This is the only method that reads the encrypted token from disk.
     */
    @JavascriptInterface
    public String unlock() {
        if (!trusted()) {
            return error("untrusted", "Biometric access is only available inside TOWEMS.");
        }
        synchronized (this) {
            if (unlockedRefresh == null) {
                if (!hasStoredToken()) {
                    return error("no_token", "Set up biometric sign-in from the web first.");
                }
                if (!isAvailable()) {
                    return error("unavailable", "No enrolled fingerprint or face is available.");
                }
                if (!promptForBiometric()) {
                    return error("cancelled", "Biometric authentication was cancelled.");
                }
                if (!decryptIntoMemory()) {
                    clearStoredToken();
                    return error("no_token", "The saved biometric session is no longer readable.");
                }
                locked = false;
            }
            return refreshWithToken();
        }
    }

    /**
     * Renew while the process is already unlocked. Deliberately never reads the
     * encrypted token, so a fresh process cannot silently skip the prompt.
     */
    @JavascriptInterface
    public String refreshAccessToken() {
        if (!trusted()) {
            return error("untrusted", "Biometric access is only available inside TOWEMS.");
        }
        synchronized (this) {
            if (unlockedRefresh == null) {
                return error("locked", "Unlock with your fingerprint or face to continue.");
            }
            return refreshWithToken();
        }
    }

    private boolean hasStoredToken() {
        return prefs.contains(KEY_CIPHERTEXT) && prefs.contains(KEY_IV);
    }

    private void clearStoredToken() {
        unlockedRefresh = null;
        unlockedUsername = null;
        locked = true;
        prefs.edit()
                .remove(KEY_CIPHERTEXT)
                .remove(KEY_IV)
                .remove(KEY_USERNAME)
                .apply();
    }

    private boolean promptForBiometric() {
        CountDownLatch latch = new CountDownLatch(1);
        AtomicBoolean approved = new AtomicBoolean(false);

        activity.runOnUiThread(() -> {
            try {
                BiometricPrompt prompt = new BiometricPrompt(
                        activity,
                        ContextCompat.getMainExecutor(activity),
                        new BiometricPrompt.AuthenticationCallback() {
                            @Override
                            public void onAuthenticationSucceeded(
                                    BiometricPrompt.AuthenticationResult result) {
                                approved.set(true);
                                latch.countDown();
                            }

                            @Override
                            public void onAuthenticationError(
                                    int errorCode, CharSequence errString) {
                                latch.countDown();
                            }

                            @Override
                            public void onAuthenticationFailed() {
                                // The prompt stays open for another attempt.
                            }
                        });
                BiometricPrompt.PromptInfo info =
                        new BiometricPrompt.PromptInfo.Builder()
                                .setTitle("Unlock TOWEMS")
                                .setSubtitle("Use your fingerprint or face to continue")
                                .setAllowedAuthenticators(
                                        BiometricManager.Authenticators.BIOMETRIC_STRONG)
                                .setNegativeButtonText("Use password")
                                .setConfirmationRequired(false)
                                .build();
                prompt.authenticate(info);
            } catch (RuntimeException error) {
                Log.w(TAG, "biometric prompt could not start", error);
                latch.countDown();
            }
        });

        try {
            return latch.await(90, TimeUnit.SECONDS) && approved.get();
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    private boolean decryptIntoMemory() {
        try {
            byte[] encrypted = Base64.decode(
                    prefs.getString(KEY_CIPHERTEXT, ""), Base64.NO_WRAP);
            byte[] iv = Base64.decode(prefs.getString(KEY_IV, ""), Base64.NO_WRAP);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, secretKey(), new GCMParameterSpec(GCM_TAG_BITS, iv));
            unlockedRefresh = new String(cipher.doFinal(encrypted), StandardCharsets.UTF_8);
            unlockedUsername = prefs.getString(KEY_USERNAME, "");
            return !unlockedRefresh.isEmpty();
        } catch (Exception error) {
            Log.w(TAG, "could not decrypt refresh token", error);
            unlockedRefresh = null;
            unlockedUsername = null;
            return false;
        }
    }

    private String refreshWithToken() {
        HttpURLConnection connection = null;
        try {
            JSONObject body = new JSONObject();
            body.put("refresh_token", unlockedRefresh);
            body.put("device_id", deviceId());
            byte[] payload = body.toString().getBytes(StandardCharsets.UTF_8);

            connection = (HttpURLConnection) new URL(BASE_URL + "/api/auth/mobile/refresh/")
                    .openConnection();
            connection.setRequestMethod("POST");
            connection.setDoOutput(true);
            connection.setConnectTimeout(15_000);
            connection.setReadTimeout(20_000);
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("User-Agent", "TOWEMS-Android/1.0");
            connection.setFixedLengthStreamingMode(payload.length);
            try (OutputStream out = connection.getOutputStream()) {
                out.write(payload);
            }

            int code = connection.getResponseCode();
            String response = read(connection, code);
            JSONObject json = response == null ? new JSONObject() : new JSONObject(response);
            if (code == 200) {
                String access = json.optString("access_token", "");
                String rotated = json.optString("refresh_token", "");
                if (access.isEmpty() || rotated.isEmpty()) {
                    return error("reauth_required", "The server did not return a session.");
                }
                // The rotation replaces the stored ciphertext atomically from the
                // app's point of view: the old token stops working server-side.
                unlockedRefresh = rotated;
                Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
                cipher.init(Cipher.ENCRYPT_MODE, secretKey());
                byte[] encrypted = cipher.doFinal(rotated.getBytes(StandardCharsets.UTF_8));
                prefs.edit()
                        .putString(KEY_CIPHERTEXT, Base64.encodeToString(encrypted, Base64.NO_WRAP))
                        .putString(KEY_IV, Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP))
                        .putString(KEY_USERNAME, unlockedUsername == null ? "" : unlockedUsername)
                        .apply();
                locked = false;
                return success(access, json.optInt("expires_in", 900));
            }

            String reason = code == 403 ? "device_revoked" : "reauth_required";
            if (code == 401 || code == 403) {
                clearStoredToken();
            }
            return error(reason, json.optString("detail", "Sign in again."));
        } catch (Exception network) {
            Log.w(TAG, "refresh request failed", network);
            return error("network", "Could not reach TOWEMS. Try again.");
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    private String read(HttpURLConnection connection, int code) {
        try (InputStream stream = code >= 400
                ? connection.getErrorStream()
                : connection.getInputStream()) {
            if (stream == null) {
                return null;
            }
            byte[] buffer = new byte[16 * 1024];
            StringBuilder text = new StringBuilder();
            int read;
            while ((read = stream.read(buffer)) > 0) {
                text.append(new String(buffer, 0, read, StandardCharsets.UTF_8));
            }
            return text.toString();
        } catch (Exception error) {
            return null;
        }
    }

    private String deviceId() {
        SharedPreferences alerts = activity.getSharedPreferences(
                AlertService.PREFS_NAME, Context.MODE_PRIVATE);
        String id = alerts.getString(KEY_DEVICE_ID, null);
        if (id == null || id.isEmpty()) {
            id = UUID.randomUUID().toString().replace("-", "");
            alerts.edit().putString(KEY_DEVICE_ID, id).apply();
        }
        return id;
    }

    private SecretKey secretKey() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (!store.containsAlias(KEY_ALIAS)) {
            KeyGenerator generator = KeyGenerator.getInstance(
                    KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
            KeyGenParameterSpec spec = new KeyGenParameterSpec.Builder(
                    KEY_ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build();
            generator.init(spec);
            generator.generateKey();
        }
        return (SecretKey) store.getKey(KEY_ALIAS, null);
    }
}
