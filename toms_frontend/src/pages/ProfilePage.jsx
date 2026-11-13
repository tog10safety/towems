import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import {
  Badge,
  Button,
  Card,
  Detail,
  ErrorBanner,
  Field,
  Input,
  Loading,
  Select,
  Textarea,
} from "../components/ui.jsx";
import { useApi } from "../hooks/useApi.js";

/**
 * Self-service profile (spec §3).
 *
 * Only the fields the backend declares editable are rendered as inputs. Role,
 * unit and status are displayed read-only: the API rejects any attempt to
 * change them, so the UI does not pretend otherwise.
 */
const PROFILE_DETAIL_FIELDS = [
  "email",
  "sex",
  "date_of_birth",
  "blood_type",
  "home_address",
  "emergency_contact_name",
  "emergency_contact_number",
  "afsc_primary",
  "afsc_secondary",
  "afsc_tertiary",
  "highest_education",
  "civil_status",
];

const PROFILE_DATE_FIELDS = ["date_of_birth"];

const SEX_OPTIONS = ["Male", "Female"];
const BLOOD_TYPE_OPTIONS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"];
const CIVIL_STATUS_OPTIONS = [
  "Single",
  "Married",
  "Widowed",
  "Separated",
  "Annulled",
];
const EDUCATION_OPTIONS = [
  "Elementary",
  "High School",
  "Senior High School",
  "Vocational / Technical",
  "Associate Degree",
  "Bachelor's Degree",
  "Master's Degree",
  "Doctorate",
];

function blankProfileForm(data) {
  const form = {
    contact_number: data?.contact_number || "",
    bio: data?.bio || "",
  };
  for (const field of PROFILE_DETAIL_FIELDS) form[field] = data?.[field] || "";
  return form;
}

export default function ProfilePage() {
  const { refresh, permissions } = useAuth();
  const { data, loading, error, reload, setData } = useApi("/auth/profile/");

  const [form, setForm] = useState(blankProfileForm(null));
  const [fieldErrors, setFieldErrors] = useState({});
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [onboardingBusy, setOnboardingBusy] = useState(false);
  const [avatarVersion, setAvatarVersion] = useState(Date.now());
  const [signatureVersion, setSignatureVersion] = useState(Date.now());
  const avatarInput = useRef(null);
  const signatureInput = useRef(null);
  const signatureSection = useRef(null);

  useEffect(() => {
    if (!data) return;
    setForm(blankProfileForm(data));
  }, [data]);

  // Signing an MRM without a signature sends the user here with #signature, so
  // the section they need is on screen instead of somewhere down the page.
  useEffect(() => {
    if (data && window.location.hash === "#signature") {
      signatureSection.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [data]);

  async function saveProfile(event) {
    event.preventDefault();
    setBusy(true);
    setActionError("");
    setFieldErrors({});
    setNotice("");
    try {
      // Send only what actually changed. Legacy records can hold values such as
      // "N/A" in contact_number; re-submitting them unchanged would fail
      // validation even though the user edited something else.
      const payload = {};
      if (form.contact_number !== (data.contact_number || "")) {
        payload.contact_number = form.contact_number;
      }
      if (form.bio !== (data.bio || "")) payload.bio = form.bio;
      for (const field of PROFILE_DETAIL_FIELDS) {
        if (form[field] !== (data[field] || "")) {
          payload[field] = PROFILE_DATE_FIELDS.includes(field)
            ? form[field] || null
            : form[field];
        }
      }
      if (!Object.keys(payload).length) {
        setNotice("No changes to save.");
        return;
      }
      const updated = await api.patch("/auth/profile/", payload);
      setData(updated);
      setNotice("Profile updated.");
    } catch (err) {
      setFieldErrors(err.fieldErrors || {});
      setActionError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function uploadImage(kind, file) {
    if (!file) return;
    const field = kind === "avatar" ? "avatar" : "signature";
    const body = new FormData();
    body.append(field, file);
    setActionError("");
    setNotice("");
    try {
      const updated = await api.upload(`/auth/profile/${kind}/`, body);
      setData(updated);
      if (kind === "avatar") {
        setAvatarVersion(Date.now());
        if (avatarInput.current) avatarInput.current.value = "";
      } else {
        setSignatureVersion(Date.now());
        if (signatureInput.current) signatureInput.current.value = "";
      }
      setNotice(kind === "avatar" ? "Profile picture updated." : "Signature updated.");
      refresh();
    } catch (err) {
      setActionError(err.message);
    }
  }

  async function removeImage(kind) {
    const label = kind === "avatar" ? "profile picture" : "signature";
    if (!window.confirm(`Remove your ${label}?`)) return;
    setActionError("");
    try {
      const updated = await api.del(`/auth/profile/${kind}/`);
      setData(updated);
      if (kind === "avatar") setAvatarVersion(Date.now());
      else setSignatureVersion(Date.now());
      setNotice(`Removed your ${label}.`);
      refresh();
    } catch (err) {
      setActionError(err.message);
    }
  }

  async function completeOnboarding() {
    setOnboardingBusy(true);
    setActionError("");
    setNotice("");
    try {
      const updated = await api.post("/auth/onboarding/complete/", {});
      setData(updated);
      setNotice("Setup complete. Welcome to TOWEMS.");
      refresh();
    } catch (err) {
      setActionError(err.message);
    } finally {
      setOnboardingBusy(false);
    }
  }

  const onboardingReady =
    data && !data.password_change_required && data.avatar_url && data.has_signature;

  if (loading && !data) return <Loading label="Loading your profile…" />;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>My Profile</h1>
          <p>
            Update your own contact details, picture and signature. Role, unit and permissions are
            managed by an administrator.
          </p>
        </div>
        {/* The Super Administrator's role is red so the seat is recognisable
            at a glance; every other account keeps the blue role badge. */}
        {data && (
          <Badge tone={permissions.is_system_owner ? "danger" : "info"}>
            {data.authority}
          </Badge>
        )}
      </div>

      <ErrorBanner message={error || actionError} onRetry={error ? reload : undefined} />
      {notice && <div className="alert alert-success">{notice}</div>}

      {data?.onboarding_required && (
        <div className="alert alert-warning">
          <h2 style={{ marginTop: 0 }}>Finish setting up your account</h2>
          <p>
            Your unit registered you with a temporary password. Complete these three steps
            before using TOWEMS:
          </p>
          <ul>
            <li>
              {data.password_change_required ? "☐" : "☑"} Change the initial password
            </li>
            <li>{data.avatar_url ? "☑" : "☐"} Upload a profile picture</li>
            <li>{data.has_signature ? "☑" : "☐"} Upload your signature</li>
          </ul>
          <p className="hint">
            You can also update your contact, personal, emergency and AFSC details below.
          </p>
          <Button onClick={completeOnboarding} disabled={!onboardingReady || onboardingBusy}>
            {onboardingBusy ? "Finishing…" : "Finish setup"}
          </Button>
        </div>
      )}

      {data && (
        <div className="grid grid-2">
          <Card title="Account">
            <div className="profile-head">
              <Avatar data={data} version={avatarVersion} />
              <div>
                <strong>{data.full_name}</strong>
                <p className="hint">@{data.username}</p>
                <div className="btn-row" style={{ marginTop: 8 }}>
                  <input
                    ref={avatarInput}
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    hidden
                    onChange={(event) => uploadImage("avatar", event.target.files?.[0])}
                  />
                  <Button size="sm" variant="ghost" onClick={() => avatarInput.current?.click()}>
                    {data.avatar_url ? "Replace picture" : "Upload picture"}
                  </Button>
                  {data.avatar_url && (
                    <Button size="sm" variant="ghost" onClick={() => removeImage("avatar")}>
                      Remove
                    </Button>
                  )}
                </div>
                <p className="hint">PNG, JPEG or WebP up to 2 MiB.</p>
              </div>
            </div>

            <dl className="details">
              <Detail label="Rank">{data.rank || "—"}</Detail>
              <Detail label="Designation">{data.designation || "—"}</Detail>
              <Detail label="Serial number">{data.serial_number || "—"}</Detail>
              <Detail label="Branch">{data.branch_of_service || "—"}</Detail>
              <Detail label="Unit">
                {data.unit_acronym || data.unit_name || "Unassigned"}
              </Detail>
              <Detail label="Roles">
                {data.roles?.length ? data.roles.join(", ") : "—"}
              </Detail>
              <Detail label="Appointments">
                {data.appointments?.length
                  ? data.appointments
                      .map((item) => `${item.role_label} (${item.unit_acronym || item.unit_id})`)
                      .join(", ")
                  : "None"}
              </Detail>
              <Detail label="Sections">
                {data.sections?.length
                  ? data.sections
                      .map((item) => `${item.code} ${item.name} (${item.assignment_type})`)
                      .join(", ")
                  : "None assigned"}
              </Detail>
              <Detail label="Password">
                {data.password_change_required ? (
                  <Badge tone="warning">Change required</Badge>
                ) : (
                  <Badge tone="success">Current</Badge>
                )}
              </Detail>
            </dl>
          </Card>

          <Card title="Editable details">
            <form onSubmit={saveProfile}>
              <div className="form-grid">
                <Field
                  label="Contact number"
                  error={fieldErrors.contact_number}
                  hint="Digits, spaces, '+' and '-' only."
                >
                  <Input
                    value={form.contact_number}
                    onChange={(event) =>
                      setForm({ ...form, contact_number: event.target.value })
                    }
                    placeholder="+63 917 000 0000"
                  />
                </Field>
                <Field label="Email address" error={fieldErrors.email}>
                  <Input
                    type="email"
                    value={form.email}
                    onChange={(event) => setForm({ ...form, email: event.target.value })}
                    placeholder="name@example.com"
                  />
                </Field>
                <Field label="Home address" className="span-2" error={fieldErrors.home_address}>
                  <Textarea
                    rows={2}
                    value={form.home_address}
                    onChange={(event) =>
                      setForm({ ...form, home_address: event.target.value })
                    }
                  />
                </Field>

                <h3 className="span-2">Personal</h3>
                <Field label="Sex / gender" error={fieldErrors.sex}>
                  <Select
                    value={form.sex}
                    onChange={(event) => setForm({ ...form, sex: event.target.value })}
                  >
                    <option value="">Select…</option>
                    {SEX_OPTIONS.map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Date of birth" error={fieldErrors.date_of_birth}>
                  <Input
                    type="date"
                    value={form.date_of_birth}
                    onChange={(event) =>
                      setForm({ ...form, date_of_birth: event.target.value })
                    }
                  />
                </Field>
                <Field label="Blood type" error={fieldErrors.blood_type}>
                  <Select
                    value={form.blood_type}
                    onChange={(event) =>
                      setForm({ ...form, blood_type: event.target.value })
                    }
                  >
                    <option value="">Select…</option>
                    {BLOOD_TYPE_OPTIONS.map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Civil status" error={fieldErrors.civil_status}>
                  <Select
                    value={form.civil_status}
                    onChange={(event) =>
                      setForm({ ...form, civil_status: event.target.value })
                    }
                  >
                    <option value="">Select…</option>
                    {CIVIL_STATUS_OPTIONS.map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Highest education" error={fieldErrors.highest_education}>
                  <Select
                    value={form.highest_education}
                    onChange={(event) =>
                      setForm({ ...form, highest_education: event.target.value })
                    }
                  >
                    <option value="">Select…</option>
                    {EDUCATION_OPTIONS.map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </Select>
                </Field>

                <h3 className="span-2">Emergency contact</h3>
                <Field label="Name" error={fieldErrors.emergency_contact_name}>
                  <Input
                    value={form.emergency_contact_name}
                    onChange={(event) =>
                      setForm({ ...form, emergency_contact_name: event.target.value })
                    }
                  />
                </Field>
                <Field label="Number" error={fieldErrors.emergency_contact_number}>
                  <Input
                    value={form.emergency_contact_number}
                    onChange={(event) =>
                      setForm({ ...form, emergency_contact_number: event.target.value })
                    }
                  />
                </Field>

                <h3 className="span-2">Air Force Specialty Code</h3>
                <Field label="Primary AFSC" error={fieldErrors.afsc_primary}>
                  <Input
                    value={form.afsc_primary}
                    onChange={(event) =>
                      setForm({ ...form, afsc_primary: event.target.value })
                    }
                  />
                </Field>
                <Field label="Secondary AFSC" error={fieldErrors.afsc_secondary}>
                  <Input
                    value={form.afsc_secondary}
                    onChange={(event) =>
                      setForm({ ...form, afsc_secondary: event.target.value })
                    }
                  />
                </Field>
                <Field label="Tertiary AFSC" error={fieldErrors.afsc_tertiary}>
                  <Input
                    value={form.afsc_tertiary}
                    onChange={(event) =>
                      setForm({ ...form, afsc_tertiary: event.target.value })
                    }
                  />
                </Field>

                <Field
                  label="About / duty notes"
                  className="span-2"
                  error={fieldErrors.bio}
                  hint="Maximum 2000 characters."
                >
                  <Textarea
                    value={form.bio}
                    onChange={(event) => setForm({ ...form, bio: event.target.value })}
                    rows={4}
                  />
                </Field>
              </div>
              <div className="btn-row" style={{ marginTop: 12 }}>
                <Button type="submit" disabled={busy}>
                  {busy ? "Saving…" : "Save changes"}
                </Button>
              </div>
            </form>
          </Card>

          <div id="signature" ref={signatureSection}>
          <Card title="Signature">
            <p className="hint">
              Your signature is applied to MRM certificates you sign or approve. It uses the
              existing signature record read by the PDF generator.
            </p>
            {data.has_signature ? (
              <div className="signature-box">
                <img
                  src={`${data.signature_url}?v=${signatureVersion}`}
                  alt="Your signature"
                />
              </div>
            ) : (
              <p className="hint">No signature uploaded yet.</p>
            )}
            <input
              ref={signatureInput}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              hidden
              onChange={(event) => uploadImage("signature", event.target.files?.[0])}
            />
            <div className="btn-row">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => signatureInput.current?.click()}
              >
                {data.has_signature ? "Replace signature" : "Upload signature"}
              </Button>
              {data.has_signature && (
                <Button size="sm" variant="ghost" onClick={() => removeImage("signature")}>
                  Remove
                </Button>
              )}
            </div>
          </Card>
          </div>

          <ChangePasswordCard
            onDone={() => {
              setNotice("Password updated.");
              reload();
              refresh();
            }}
          />
        </div>
      )}
    </>
  );
}

function Avatar({ data, version }) {
  if (!data.avatar_url) {
    return (
      <span className="avatar avatar-empty" aria-hidden="true">
        {(data.full_name || data.username || "?").slice(0, 1).toUpperCase()}
      </span>
    );
  }
  return (
    <img
      className="avatar"
      src={`${data.avatar_url}?v=${version}`}
      alt={`${data.full_name} profile picture`}
    />
  );
}

function ChangePasswordCard({ onDone }) {
  const [form, setForm] = useState({ current_password: "", new_password: "", confirm: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setError("");
    if (form.new_password !== form.confirm) {
      setError("The new passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await api.post("/auth/password/", {
        current_password: form.current_password,
        new_password: form.new_password,
      });
      setForm({ current_password: "", new_password: "", confirm: "" });
      onDone?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Change password">
      <ErrorBanner message={error} />
      <form onSubmit={submit}>
        <Field label="Current password" required>
          <Input
            type="password"
            value={form.current_password}
            onChange={(event) => setForm({ ...form, current_password: event.target.value })}
            required
            autoComplete="current-password"
          />
        </Field>
        <Field
          label="New password"
          required
          hint="Minimum 8 characters with upper case, lower case, a digit and a symbol."
        >
          <Input
            type="password"
            value={form.new_password}
            onChange={(event) => setForm({ ...form, new_password: event.target.value })}
            required
            autoComplete="new-password"
          />
        </Field>
        <Field label="Confirm new password" required>
          <Input
            type="password"
            value={form.confirm}
            onChange={(event) => setForm({ ...form, confirm: event.target.value })}
            required
            autoComplete="new-password"
          />
        </Field>
        <Button type="submit" disabled={busy}>
          {busy ? "Updating…" : "Update password"}
        </Button>
      </form>
    </Card>
  );
}
