package online.mrmsystem.towems;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.TextUtils;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import androidx.core.content.ContextCompat;

/**
 * A one-line answer, always on screen, to "is this phone actually listening?"
 *
 * The web app can say nothing about a watcher that Android silenced: its bell
 * still polls while it is open, so the page looks healthy on a phone that will
 * never buzz. This strip sits above the page and reports what the phone itself
 * knows - notifications allowed, alert key present, service alive, last reply
 * from the server - and turns red or amber the moment one of those is wrong,
 * which is exactly the case a person cannot otherwise see.
 *
 * It also carries the one test that needs no account on the other end: a
 * notification posted through the very channel a message would use, so seeing
 * it (and feeling it) proves the delivery path on this phone.
 */
public class WatcherBar extends LinearLayout implements AlertService.StateListener {

    private static final String TAG = "TOWEMS/WatcherBar";

    private static final int COLOR_LIVE = 0xFF166534;
    private static final int COLOR_WARN = 0xFF8A4B08;
    private static final int COLOR_BAD = 0xFF991B1B;

    private final Handler ui = new Handler(Looper.getMainLooper());
    private final TextView title;
    private final TextView detail;
    private final TextView test;
    private int statusBarInset;
    private WatcherStatus status;

    public WatcherBar(Context context) {
        super(context);
        setOrientation(HORIZONTAL);
        setGravity(Gravity.CENTER_VERTICAL);
        setPadding(dp(12), dp(10), dp(8), dp(10));
        setBackgroundColor(COLOR_WARN);

        TextView dot = new TextView(context);
        dot.setText("\u25CF");
        dot.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11);
        dot.setTextColor(0xFFFFFFFF);
        LayoutParams dotParams = new LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        dotParams.rightMargin = dp(10);

        title = new TextView(context);
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setTextColor(0xFFFFFFFF);
        title.setMaxLines(1);
        title.setEllipsize(TextUtils.TruncateAt.END);

        detail = new TextView(context);
        detail.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11);
        detail.setTextColor(0xE6FFFFFF);
        detail.setMaxLines(2);
        detail.setEllipsize(TextUtils.TruncateAt.END);

        LinearLayout column = new LinearLayout(context);
        column.setOrientation(VERTICAL);
        column.addView(title);
        column.addView(detail);

        test = new TextView(context);
        test.setText(R.string.watcher_test);
        test.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11);
        test.setTypeface(Typeface.DEFAULT_BOLD);
        test.setTextColor(0xFFFFFFFF);
        test.setGravity(Gravity.CENTER);
        test.setPadding(dp(12), dp(7), dp(12), dp(7));
        test.setBackground(round(0x33FFFFFF));
        test.setContentDescription(context.getString(R.string.watcher_send_test));
        test.setOnClickListener(view -> fireTest());

        addView(dot, dotParams);
        addView(column, new LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        addView(test, new LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        setOnClickListener(view -> showDetails());
        // Measured after layout, not when the window is created: where the strip
        // begins on screen is the ground truth for whether the system already
        // moved the page below the clock or the window draws behind it.
        addOnLayoutChangeListener(
                (view, left, top, right, bottom, oldLeft, oldTop, oldRight, oldBottom) ->
                        fitBelowStatusBar());
        refresh();
    }

    private void fitBelowStatusBar() {
        int[] origin = new int[2];
        getLocationOnScreen(origin);
        int id = getResources().getIdentifier("status_bar_height", "dimen", "android");
        int statusBar = id > 0 ? getResources().getDimensionPixelSize(id) : 0;
        int wanted = Math.max(0, statusBar - origin[1]);
        if (wanted != statusBarInset) {
            setStatusBarInset(wanted);
        }
    }

    /**
     * Room for the system clock when the window draws behind the status bar.
     *
     * The shell is edge to edge on Android 15 and later, so a strip pinned to
     * the top would otherwise put its first line under the clock.
     */
    public void setStatusBarInset(int pixels) {
        statusBarInset = Math.max(0, pixels);
        setPadding(dp(12), dp(10) + statusBarInset, dp(8), dp(10));
    }

    @Override
    protected void onAttachedToWindow() {
        super.onAttachedToWindow();
        AlertService.watch(this);
        refresh();
    }

    @Override
    protected void onDetachedFromWindow() {
        AlertService.unwatch(this);
        super.onDetachedFromWindow();
    }

    @Override
    public void onWatcherState() {
        refresh();
    }

    /** Read the phone's state on the calling thread and draw it on the UI thread. */
    public void refresh() {
        ui.post(this::apply);
    }

    private void apply() {
        Context context = getContext();
        if (context == null) {
            return;
        }
        status = WatcherStatus.read(context);

        switch (status.level) {
            case LIVE:
                setBackgroundColor(COLOR_LIVE);
                title.setText(R.string.watcher_live_title);
                detail.setText(context.getString(
                        R.string.watcher_live_detail, ago(context, status.lastCheckMs)));
                break;
            case BLOCKED:
                setBackgroundColor(COLOR_BAD);
                title.setText(R.string.watcher_blocked_title);
                detail.setText(R.string.watcher_blocked_detail);
                break;
            case STOPPED:
                setBackgroundColor(COLOR_WARN);
                title.setText(R.string.watcher_stopped_title);
                detail.setText(R.string.watcher_stopped_detail);
                break;
            case NO_TOKEN:
                setBackgroundColor(COLOR_BAD);
                title.setText(R.string.watcher_no_token_title);
                detail.setText(R.string.watcher_no_token_detail);
                break;
            case SIGNING_IN:
                setBackgroundColor(COLOR_WARN);
                title.setText(R.string.watcher_signing_in_title);
                detail.setText(R.string.watcher_signing_in_detail);
                break;
            case OFFLINE:
                setBackgroundColor(COLOR_BAD);
                title.setText(R.string.watcher_offline_title);
                detail.setText(status.lastOkMs == 0
                        ? context.getString(R.string.watcher_offline_never)
                        : context.getString(R.string.watcher_offline_detail,
                                ago(context, status.lastOkMs)));
                break;
            case STARTING:
            default:
                setBackgroundColor(COLOR_WARN);
                title.setText(R.string.watcher_starting_title);
                detail.setText(R.string.watcher_starting_detail);
                break;
        }
    }

    private void showDetails() {
        Context context = getContext();
        if (context == null) {
            return;
        }
        WatcherStatus snapshot = WatcherStatus.read(context);

        LinearLayout box = new LinearLayout(context);
        box.setOrientation(VERTICAL);
        box.setPadding(dp(22), dp(4), dp(22), 0);

        TextView lines = new TextView(context);
        lines.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        lines.setText(summary(context, snapshot));
        box.addView(lines);

        TextView sendTest = action(context, R.string.watcher_send_test);
        box.addView(sendTest);

        TextView checkNow = action(context, R.string.watcher_check_now);
        box.addView(checkNow);

        final TextView settings = snapshot.notificationsBlocked
                ? action(context, R.string.watcher_settings)
                : null;
        if (settings != null) {
            box.addView(settings);
        }

        final AlertDialog dialog = new AlertDialog.Builder(context)
                .setTitle(R.string.watcher_dialog_title)
                .setView(box)
                .setNegativeButton(R.string.watcher_close, null)
                .create();

        sendTest.setOnClickListener(view -> {
            dialog.dismiss();
            fireTest();
        });
        checkNow.setOnClickListener(view -> {
            dialog.dismiss();
            checkNow();
        });
        if (settings != null) {
            settings.setOnClickListener(view -> {
                dialog.dismiss();
                openNotificationSettings(context);
            });
        }
        dialog.show();
    }

    private String summary(Context context, WatcherStatus snapshot) {
        StringBuilder text = new StringBuilder();
        text.append(context.getString(R.string.watcher_line_watcher,
                context.getString(snapshot.serviceRunning
                        ? R.string.watcher_running : R.string.watcher_off))).append('\n');

        int notifications = snapshot.channelSilenced
                ? R.string.watcher_silenced
                : snapshot.notificationsBlocked ? R.string.watcher_blocked_value : R.string.watcher_allowed;
        text.append(context.getString(
                R.string.watcher_line_notifications, context.getString(notifications))).append('\n');

        text.append(context.getString(R.string.watcher_line_key,
                context.getString(snapshot.tokenPresent
                        ? R.string.watcher_present : R.string.watcher_missing))).append('\n');

        text.append(context.getString(R.string.watcher_line_server,
                snapshot.lastOkMs == 0
                        ? context.getString(R.string.watcher_never)
                        : ago(context, snapshot.lastOkMs))).append('\n');

        text.append(context.getString(R.string.watcher_line_battery,
                context.getString(snapshot.batteryExempt
                        ? R.string.watcher_exempt : R.string.watcher_restricted)));

        if (snapshot.lastError != null && !snapshot.lastError.isEmpty()) {
            text.append('\n').append(context.getString(
                    R.string.watcher_line_error, snapshot.lastError));
        }
        return text.toString();
    }

    private TextView action(Context context, int label) {
        TextView button = new TextView(context);
        button.setText(label);
        button.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        button.setTextColor(0xFF1D4ED8);
        button.setPadding(0, dp(14), 0, dp(2));
        return button;
    }

    /** Post a notification through the real channel; the phone is the proof. */
    private void fireTest() {
        Context context = getContext();
        if (context == null) {
            return;
        }
        if (AlertService.sendTestNotification(context)) {
            toast(context, R.string.watcher_test_sent);
        } else {
            // Nothing can be delivered while Android blocks the app, so send the
            // person to the one screen that can change it.
            toast(context, R.string.watcher_test_blocked);
            openNotificationSettings(context);
        }
        refresh();
    }

    private void checkNow() {
        Context context = getContext();
        if (context == null) {
            return;
        }
        try {
            ContextCompat.startForegroundService(context,
                    new Intent(context, AlertService.class)
                            .setAction(AlertService.ACTION_CHECK_NOW));
            toast(context, R.string.watcher_checking);
        } catch (RuntimeException refused) {
            Log.i(TAG, "immediate check refused: " + refused.getClass().getSimpleName());
            toast(context, R.string.watcher_stopped_detail);
        }
        ui.postDelayed(this::refresh, 2_500);
    }

    private void toast(Context context, int message) {
        Toast.makeText(context, message, Toast.LENGTH_LONG).show();
    }

    private void openNotificationSettings(Context context) {
        Intent intent = new Intent();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            intent.setAction(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                    .putExtra(Settings.EXTRA_APP_PACKAGE, context.getPackageName());
        } else {
            intent.setAction(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                    .setData(Uri.parse("package:" + context.getPackageName()));
        }
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            context.startActivity(intent);
        } catch (ActivityNotFoundException missing) {
            try {
                context.startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                        Uri.parse("package:" + context.getPackageName()))
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            } catch (ActivityNotFoundException ignored) {
                Log.w(TAG, "no notification settings screen on this device");
            }
        }
    }

    private GradientDrawable round(int color) {
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(color);
        shape.setCornerRadius(dp(14));
        return shape;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private static String ago(Context context, long millis) {
        long seconds = Math.max(0, (System.currentTimeMillis() - millis) / 1000);
        if (seconds < 5) {
            return context.getString(R.string.watcher_just_now);
        }
        String span;
        if (seconds < 60) {
            span = seconds + " s";
        } else if (seconds < 3_600) {
            span = (seconds / 60) + " min";
        } else if (seconds < 86_400) {
            span = (seconds / 3_600) + " h";
        } else {
            span = (seconds / 86_400) + " d";
        }
        return context.getString(R.string.watcher_ago, span);
    }
}
