import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useSearchParams } from "react-router-dom";
import { api, qs } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import Icon from "../components/Icon.jsx";
import {
  Badge,
  Button,
  EmptyState,
  ErrorBanner,
  Input,
  Loading,
} from "../components/ui.jsx";
import { subscribeToPush } from "../push.js";
import { formatDate, formatTime } from "../utils/format.js";
import GroupChatApprovals from "./GroupChatApprovals.jsx";

/*
 * Private messaging.
 *
 * TOWEMS is a WSGI application (gunicorn), so there is no socket to hold open.
 * The page keeps the conversation list and the open thread fresh by polling
 * small windows: the inbox every few seconds, the open thread a little faster.
 * Sending is immediate - the message appears as soon as the server accepts it -
 * and everything else catches up within one interval, which is what "live"
 * looks like on this deployment.
 */

const INBOX_POLL_MS = 4000;
const THREAD_POLL_MS = 2500;
const TYPING_THROTTLE_MS = 2000;
const PAGE_SIZE = 40;

function initials(person) {
  const letters = `${person?.full_name || person?.serial_number || "?"}`
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("");
  return (letters || "?").toUpperCase();
}

function personMeta(person) {
  return [person?.rank, person?.unit_acronym || person?.unit_name]
    .filter(Boolean)
    .join(" · ");
}

function receiptLabel(message) {
  if (message.status === "read") return "read";
  if (message.status === "delivered") return "delivered";
  return "sent";
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/*
 * Highlight the people the sender named. The server stores who was mentioned,
 * so a rename follows the person instead of the text that was typed.
 */
function renderMentions(text, mentions) {
  const names = (mentions || [])
    .map((row) => row.full_name)
    .filter(Boolean)
    .sort((first, second) => second.length - first.length);
  if (!names.length) return text;
  const pattern = new RegExp(`(@(?:${names.map(escapeRegExp).join("|")}))`, "g");
  return text.split(pattern).map((part, index) =>
    names.some((name) => part === `@${name}`) ? (
      <mark key={index} className="mention">
        {part}
      </mark>
    ) : (
      part
    )
  );
}

function notificationsSupported() {
  return typeof window !== "undefined" && "Notification" in window;
}

/** Ask for notification permission on both promise-based and older browsers. */
function requestNotificationPermission() {
  return new Promise((resolve) => {
    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    try {
      const result = Notification.requestPermission(done);
      if (result && typeof result.then === "function") result.then(done);
    } catch {
      done("denied");
    }
  });
}

export default function MessagesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { permissions } = useAuth();
  // The Operations Center proposes a group chat from the Messages header. The
  // proposal then lives as a routing thread the Unit Commander and the
  // Administrator decide in, so only the Operations Center needs this dialog.
  const isOperationsCenter = Boolean(permissions.roles?.includes("operations_center"));

  const [conversations, setConversations] = useState([]);
  const [unitGroup, setUnitGroup] = useState(null);
  const [customGroups, setCustomGroups] = useState([]);
  const [routingThreads, setRoutingThreads] = useState([]);
  const [privateEnabled, setPrivateEnabled] = useState(true);
  const [unreadTotal, setUnreadTotal] = useState(0);
  const [gcOpen, setGcOpen] = useState(false);
  const [listLoading, setListLoading] = useState(true);
  const [error, setError] = useState("");

  const [activeId, setActiveId] = useState(null);
  const [thread, setThread] = useState(null);
  const [messages, setMessages] = useState([]);
  const [hasMore, setHasMore] = useState(false);
  const [threadLoading, setThreadLoading] = useState(false);
  const [threadError, setThreadError] = useState("");
  const [threadTerm, setThreadTerm] = useState("");

  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [requestSaving, setRequestSaving] = useState(false);
  // Reply, mentions and unsend apply to one message at a time in the thread.
  const [replyTo, setReplyTo] = useState(null);
  const [mentions, setMentions] = useState([]);
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionTerm, setMentionTerm] = useState("");
  const [mentionPeople, setMentionPeople] = useState([]);
  const [mentionLoading, setMentionLoading] = useState(false);

  const [peopleOpen, setPeopleOpen] = useState(false);
  const [peopleTerm, setPeopleTerm] = useState("");
  const [people, setPeople] = useState([]);
  const [peopleLoading, setPeopleLoading] = useState(false);

  const [globalTerm, setGlobalTerm] = useState("");
  const [searchResults, setSearchResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [notice, setNotice] = useState("");

  // Long-press / right-click actions, archive filter and multi-select.
  const [menuRow, setMenuRow] = useState(null);
  const [selecting, setSelecting] = useState(false);
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [showArchived, setShowArchived] = useState(false);
  const [stateBusy, setStateBusy] = useState(false);
  const longPressTimer = useRef(null);
  const longPressStart = useRef(null);
  const suppressClick = useRef(false);
  // The menu opens while the finger is still down; the release click must not
  // land on the backdrop and close it again.
  const menuOpenedAt = useRef(0);

  const [membersOpen, setMembersOpen] = useState(false);
  const [members, setMembers] = useState([]);
  const [membersLoading, setMembersLoading] = useState(false);
  // Only the unit channel's Operations Center may change View/Post.
  const [memberCanManage, setMemberCanManage] = useState(false);
  const [memberSavingId, setMemberSavingId] = useState(null);
  const [memberNotice, setMemberNotice] = useState("");

  const [permission, setPermission] = useState(
    notificationsSupported() ? Notification.permission : "unsupported"
  );
  // Feedback for the Enable notifications button, shown beside the thread.
  const [notificationNotice, setNotificationNotice] = useState("");

  const bodyRef = useRef(null);
  const seenIds = useRef(new Set());
  const lastTypingSent = useRef(0);
  const autoScroll = useRef(true);

  const loadConversations = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setListLoading(true);
    try {
      const data = await api.get("/messaging/conversations/");
      setConversations(data.conversations || []);
      setUnitGroup(data.unit_group || null);
      setCustomGroups(data.custom_groups || []);
      setRoutingThreads(data.routing_threads || []);
      setPrivateEnabled(data.private_messaging_enabled !== false);
      setUnreadTotal(data.unread_total || 0);
      setError("");
    } catch (err) {
      if (!silent) setError(err.message || "Unable to load conversations.");
    } finally {
      if (!silent) setListLoading(false);
    }
  }, []);

  const loadThread = useCallback(async (id, { term = "", silent = false } = {}) => {
    if (!id) return null;
    if (!silent) setThreadLoading(true);
    try {
      const data = await api.get(
        `/messaging/conversations/${id}/messages/${qs({
          q: term,
          page_size: PAGE_SIZE,
        })}`
      );
      setThread(data.conversation);
      setHasMore(data.has_more);
      setMessages((current) => {
        if (!silent) return data.messages;
        // Keep whatever older pages were loaded, and drop the rest: the newest
        // window is authoritative for delivery/read receipts.
        const fresh = data.messages;
        const oldestFresh = fresh.length ? fresh[0].id : Infinity;
        const older = current.filter((row) => row.id < oldestFresh);
        return [...older, ...fresh];
      });
      setThreadError("");
      return data;
    } catch (err) {
      if (!silent) setThreadError(err.message || "Unable to load this conversation.");
      return null;
    } finally {
      if (!silent) setThreadLoading(false);
    }
  }, []);

  const markRead = useCallback(async (id) => {
    if (!id) return;
    try {
      const data = await api.post(`/messaging/conversations/${id}/read/`, {});
      setConversations((current) =>
        current.map((row) =>
          row.id === id ? { ...row, unread_count: 0 } : row
        )
      );
      setUnitGroup((current) =>
        current && current.id === id ? { ...current, unread_count: 0 } : current
      );
      setCustomGroups((current) =>
        current.map((row) =>
          row.id === id ? { ...row, unread_count: 0 } : row
        )
      );
      setThread((current) =>
        current && current.id === id ? data.conversation : current
      );
      setUnreadTotal(data.unread_total ?? 0);
    } catch {
      // A receipt is a courtesy; failing to record one must not break the chat.
    }
  }, []);

  // --- inbox ---------------------------------------------------------------
  useEffect(() => {
    loadConversations();
    const timer = setInterval(() => {
      if (!document.hidden) loadConversations({ silent: true });
    }, INBOX_POLL_MS);
    return () => clearInterval(timer);
  }, [loadConversations]);

  // A notification links here with ?conversation=<id>. React may already have
  // this page mounted when the next notification is opened, so this also has
  // to answer later navigations, not only the first mount.
  useEffect(() => {
    const requested = Number(searchParams.get("conversation") || 0);
    if (!requested) return;
    setActiveId(requested);
    setMessages([]);
    setThread(null);
    setThreadLoading(true);
    setThreadError("");
    autoScroll.current = true;
    const next = new URLSearchParams(searchParams);
    next.delete("conversation");
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  // --- open thread, live ---------------------------------------------------
  useEffect(() => {
    if (!activeId) {
      setMessages([]);
      setThread(null);
      return undefined;
    }
    let cancelled = false;
    seenIds.current = new Set();
    autoScroll.current = true;

    async function refresh(silent) {
      const data = await loadThread(activeId, { term: threadTerm, silent });
      if (!data || cancelled) return;
      const incoming = data.messages.filter(
        (row) => !seenIds.current.has(row.id)
      );
      data.messages.forEach((row) => seenIds.current.add(row.id));
      if (
        silent &&
        incoming.some((row) => !row.is_mine) &&
        // The freshly fetched card carries the current mute flag; the effect's
        // closure would otherwise keep the value from when the thread opened.
        !data.conversation.is_muted &&
        notificationsSupported() &&
        Notification.permission === "granted" &&
        (document.hidden || !document.hasFocus())
      ) {
        const latest = incoming[incoming.length - 1];
        const notice = new Notification(
          data.conversation.peer?.full_name ||
            data.conversation.title ||
            "New TOWEMS message",
          { body: latest.body.slice(0, 180), tag: `towems-conversation-${activeId}` }
        );
        notice.onclick = () => window.focus();
      }
      if (!document.hidden && data.conversation.unread_count > 0) {
        markRead(activeId);
      }
    }

    refresh(false);
    const timer = setInterval(() => refresh(true), THREAD_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [activeId, threadTerm, loadThread, markRead]);

  // Land on the newest bubble once the messages are actually in the DOM. The
  // fetch callback used to set scrollTop straight after setMessages, before
  // React had committed the thread, so opening a chat stayed at the oldest
  // message and the new one was below the fold. Keying on the newest id also
  // leaves "Load earlier messages" alone, because prepending keeps that id.
  const lastMessageId = messages.length ? messages[messages.length - 1].id : null;
  useLayoutEffect(() => {
    if (!activeId || !autoScroll.current) return;
    const element = bodyRef.current;
    if (!element) return;
    element.scrollTop = element.scrollHeight;
  }, [activeId, lastMessageId]);

  // --- people picker -------------------------------------------------------
  useEffect(() => {
    if (!peopleOpen) return undefined;
    const timer = setTimeout(async () => {
      setPeopleLoading(true);
      try {
        const data = await api.get(`/messaging/people/${qs({ q: peopleTerm.trim() })}`);
        setPeople(data.people || []);
      } catch (err) {
        setNotice(err.message || "Unable to search personnel.");
      } finally {
        setPeopleLoading(false);
      }
    }, 250);
    return () => clearTimeout(timer);
  }, [peopleOpen, peopleTerm]);

  // --- mention picker ------------------------------------------------------
  useEffect(() => {
    if (!mentionOpen || !activeId) return undefined;
    let cancelled = false;
    setMentionLoading(true);
    api
      .get(`/messaging/conversations/${activeId}/people/`)
      .then((data) => {
        if (!cancelled) setMentionPeople(data.people || []);
      })
      .catch((err) => {
        if (!cancelled) setThreadError(err.message || "Unable to load people.");
      })
      .finally(() => {
        if (!cancelled) setMentionLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [mentionOpen, activeId]);

  // --- actions -------------------------------------------------------------
  function openConversation(id, term = "") {
    setActiveId(id);
    setThreadTerm(term);
    setThreadError("");
    setMembersOpen(false);
    setMemberNotice("");
    setMessages([]);
    setReplyTo(null);
    setMentions([]);
    setMentionOpen(false);
    // Drop the previous conversation's card too: until this one loads, a stale
    // private thread must not stand in for a routing thread opened by id.
    setThread(null);
    setThreadLoading(true);
    // Every open starts at the newest message, even when the same thread was
    // already active and the person had scrolled up to read older ones.
    autoScroll.current = true;
    // Opening a thread leaves the inbox search behind: keeping the term in the
    // box while the pane showed conversations again was confusing.
    setSearchResults(null);
    setGlobalTerm("");
    const url = new URL(window.location.href);
    url.searchParams.set("conversation", String(id));
    window.history.replaceState({}, "", url);
  }

  function closeThread() {
    setActiveId(null);
    setThread(null);
    setReplyTo(null);
    setMentions([]);
    setMentionOpen(false);
    setMembersOpen(false);
    setMemberNotice("");
    const url = new URL(window.location.href);
    url.searchParams.delete("conversation");
    window.history.replaceState({}, "", url);
  }

  function cancelLongPress() {
    window.clearTimeout(longPressTimer.current);
    longPressStart.current = null;
  }

  function beginLongPress(row, event) {
    // A mouse gets the ordinary context menu; touch and pen get a long press.
    if (event.pointerType === "mouse") return;
    cancelLongPress();
    longPressStart.current = { x: event.clientX, y: event.clientY };
    longPressTimer.current = window.setTimeout(() => {
      suppressClick.current = true;
      menuOpenedAt.current = Date.now();
      setMenuRow(row);
    }, 500);
  }

  function moveLongPress(event) {
    if (!longPressStart.current) return;
    if (
      Math.abs(event.clientX - longPressStart.current.x) > 10 ||
      Math.abs(event.clientY - longPressStart.current.y) > 10
    ) {
      cancelLongPress();
    }
  }

  function endLongPress() {
    cancelLongPress();
    // The click that follows a long press must not also open the thread.
    window.setTimeout(() => {
      suppressClick.current = false;
    }, 0);
  }

  function handleRowClick(row) {
    if (suppressClick.current) {
      suppressClick.current = false;
      return;
    }
    if (selecting) {
      setSelectedIds((current) => {
        const next = new Set(current);
        if (next.has(row.id)) next.delete(row.id);
        else next.add(row.id);
        return next;
      });
      return;
    }
    openConversation(row.id);
  }

  function startSelecting(row) {
    setMenuRow(null);
    setSelecting(true);
    setSelectedIds(new Set([row.id]));
  }

  function cancelSelecting() {
    setSelecting(false);
    setSelectedIds(new Set());
  }

  async function applyAction(row, action) {
    setStateBusy(true);
    try {
      await api.post(`/messaging/conversations/${row.id}/state/`, { action });
      setMenuRow(null);
      if (
        (action === "delete" || action === "mark_unread") &&
        row.id === activeId
      ) {
        closeThread();
      }
      if (action === "delete") {
        setSelectedIds((current) => {
          const next = new Set(current);
          next.delete(row.id);
          return next;
        });
      }
      await loadConversations({ silent: true });
    } catch (err) {
      setNotice(err.message || "Unable to update the conversation.");
    } finally {
      setStateBusy(false);
    }
  }

  function deleteConversation(row) {
    if (
      !window.confirm(
        "Delete this conversation for you? It comes back if a new message arrives."
      )
    ) {
      return;
    }
    applyAction(row, "delete");
  }

  async function bulkAction(action) {
    const ids = [...selectedIds];
    if (!ids.length) return;
    if (
      action === "delete" &&
      !window.confirm(`Delete ${ids.length} conversation(s) for you?`)
    ) {
      return;
    }
    setStateBusy(true);
    try {
      await Promise.all(
        ids.map((id) =>
          api.post(`/messaging/conversations/${id}/state/`, { action })
        )
      );
      if (action === "delete" && ids.includes(activeId)) closeThread();
      cancelSelecting();
      await loadConversations({ silent: true });
    } catch (err) {
      setNotice(err.message || "Unable to update the conversations.");
    } finally {
      setStateBusy(false);
    }
  }

  function toggleArchived() {
    setShowArchived((current) => !current);
    cancelSelecting();
  }

  async function startWith(person) {
    if (!privateEnabled) return;
    try {
      const conversation = await api.post("/messaging/conversations/", {
        user_id: person.id,
      });
      setPeopleOpen(false);
      setPeopleTerm("");
      openConversation(conversation.id);
      loadConversations({ silent: true });
    } catch (err) {
      setNotice(err.message || "Unable to start that conversation.");
    }
  }

  async function handleSend(event) {
    event.preventDefault();
    const body = draft.trim();
    if (!body || !activeId || sending) return;
    setSending(true);
    try {
      const payload = { body };
      if (replyTo) payload.reply_to = replyTo.id;
      if (mentions.length) payload.mention_ids = mentions.map((row) => row.id);
      const message = await api.post(
        `/messaging/conversations/${activeId}/messages/`,
        payload
      );
      setMessages((current) => [...current, message]);
      seenIds.current.add(message.id);
      lastTypingSent.current = 0;
      setDraft("");
      setReplyTo(null);
      setMentions([]);
      setMentionOpen(false);
      autoScroll.current = true;
      requestAnimationFrame(() => {
        const element = bodyRef.current;
        if (element) element.scrollTop = element.scrollHeight;
      });
      loadConversations({ silent: true });
    } catch (err) {
      setThreadError(err.message || "The message was not sent.");
    } finally {
      setSending(false);
    }
  }

  async function decideRequest(decision) {
    if (!activeId || !thread?.request_id || requestSaving) return;
    setRequestSaving(true);
    setThreadError("");
    try {
      await api.post(
        `/messaging/group-chat-requests/${thread.request_id}/${decision}/`,
        { note: "" }
      );
      // Reload the thread so the decision message and the new stage are shown
      // where the authority acted; the inbox catches up in the background.
      await loadThread(activeId, { term: threadTerm });
      loadConversations({ silent: true });
    } catch (err) {
      setThreadError(err.message || "Unable to record that decision.");
    } finally {
      setRequestSaving(false);
    }
  }

  async function handleLoadOlder() {
    if (!activeId || !messages.length) return;
    try {
      const data = await api.get(
        `/messaging/conversations/${activeId}/messages/${qs({
          before_id: messages[0].id,
          page_size: PAGE_SIZE,
        })}`
      );
      setMessages((current) => [...data.messages, ...current]);
      setHasMore(data.has_more);
    } catch (err) {
      setThreadError(err.message || "Unable to load earlier messages.");
    }
  }

  function handleDraft(event) {
    const previous = draft;
    const value = event.target.value;
    const caret = event.target.selectionStart ?? value.length;
    // Only a freshly typed "@" opens the picker. Continuing a sentence after a
    // mention that is already in the box must not reopen it, even though that
    // mention's name still sits behind the caret.
    const typedAt =
      value.length === previous.length + 1 &&
      value[caret - 1] === "@" &&
      (caret === 1 || /[\s(]/.test(value[caret - 2]));
    if (!mentionOpen && typedAt && activeConversation?.kind !== "private" && canPost) {
      setMentionTerm("");
      setMentionOpen(true);
    } else if (mentionOpen) {
      const trailing = value.slice(0, caret).match(/@([^@\n]{0,30})$/);
      if (trailing) setMentionTerm(trailing[1]);
      else setMentionOpen(false);
    }
    setDraft(value);
    if (!activeId || !value.trim() || activeConversation?.kind !== "private") return;
    const now = Date.now();
    if (now - lastTypingSent.current < TYPING_THROTTLE_MS) return;
    lastTypingSent.current = now;
    api
      .post(`/messaging/conversations/${activeId}/typing/`, { typing: true })
      .catch(() => {});
  }

  function handleComposerKey(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSend(event);
    }
  }

  function startReply(message) {
    setReplyTo(message);
    const box = document.querySelector(".msg-composer textarea");
    if (box) box.focus();
  }

  function chooseMention(person) {
    setMentions((current) =>
      current.some((row) => row.id === person.id) ? current : [...current, person]
    );
    setDraft((current) => {
      if (/@([^@\n]{0,30})$/.test(current)) {
        return current.replace(/@([^@\n]{0,30})$/, `@${person.full_name} `);
      }
      const spacer = current && !current.endsWith(" ") ? " " : "";
      return `${current}${spacer}@${person.full_name} `;
    });
    setMentionOpen(false);
    setMentionTerm("");
  }

  function removeMention(id) {
    setMentions((current) => current.filter((row) => row.id !== id));
  }

  async function unsendMessage(message) {
    if (!activeId || !message.can_unsend) return;
    const confirmed = window.confirm(
      "Unsend this message? Everyone will see that it was unsent."
    );
    if (!confirmed) return;
    try {
      const updated = await api.post(
        `/messaging/conversations/${activeId}/messages/${message.id}/unsend/`,
        {}
      );
      setMessages((current) =>
        current.map((row) => (row.id === updated.id ? updated : row))
      );
      if (replyTo?.id === updated.id) setReplyTo(null);
      loadConversations({ silent: true });
    } catch (err) {
      setThreadError(err.message || "That message could not be unsent.");
    }
  }

  async function runSearch(event) {
    event.preventDefault();
    const term = globalTerm.trim();
    if (term.length < 2) {
      setNotice("Type at least two characters to search messages.");
      return;
    }
    setSearching(true);
    setNotice("");
    try {
      const data = await api.get(`/messaging/search/${qs({ q: term })}`);
      setSearchResults(data);
    } catch (err) {
      setNotice(err.message || "Search failed.");
    } finally {
      setSearching(false);
    }
  }

  async function openMembers() {
    if (!activeId) return;
    setMembersOpen(true);
    setMemberNotice("");
    setMembersLoading(true);
    try {
      // Every group chat exposes its roster; the response also says whether
      // this viewer may edit the unit channel's View/Post permissions.
      const data = await api.get(`/messaging/conversations/${activeId}/members/`);
      setMembers(data.members || []);
      setMemberCanManage(Boolean(data.can_manage));
    } catch (err) {
      setMembers([]);
      setMemberCanManage(false);
      setMemberNotice(err.message || "Unable to load the group members.");
    } finally {
      setMembersLoading(false);
    }
  }

  async function updateMember(row, field, value) {
    if (!activeId) return;
    setMemberSavingId(row.id);
    setMemberNotice("");
    const payload = { [field]: value };
    if (field === "can_post" && value) payload.can_view = true;
    if (field === "can_view" && !value) payload.can_post = false;
    try {
      const updated = await api.patch(
        `/messaging/unit-group/${activeId}/members/${row.id}/`,
        payload
      );
      setMembers((current) =>
        current.map((member) => (member.id === row.id ? updated : member))
      );
    } catch (err) {
      setMemberNotice(err.message || "Unable to change that permission.");
    } finally {
      setMemberSavingId(null);
    }
  }

  async function enableNotifications() {
    setNotificationNotice("");
    if (!notificationsSupported()) {
      setNotificationNotice("This browser does not support notifications.");
      return;
    }
    const result = await requestNotificationPermission();
    setPermission(result);
    if (result === "denied") {
      // The browser remembers an earlier block and will not prompt again, so
      // the button used to look dead. Say what to change instead.
      setNotificationNotice(
        "Notifications are blocked for this site. Allow them in your browser's site settings, then try again."
      );
      return;
    }
    if (result !== "granted") {
      setNotificationNotice("Notification permission was not granted.");
      return;
    }
    setNotificationNotice("Notifications are on for this browser.");
    // The same permission also lets the service worker alert a closed tab; a
    // server without VAPID keys still keeps the in-app/while-open behaviour.
    let outcome = { ok: false, reason: "server" };
    try {
      outcome = await subscribeToPush();
    } catch {
      // A browser without a usable push service still keeps the while-open path.
    }
    if (!outcome.ok) {
      setNotificationNotice(
        outcome.reason === "disabled"
          ? "Alerts are enabled while TOWEMS is open. Closed-tab push is not configured on this server."
          : "Alerts are enabled while TOWEMS is open. Closed-tab push could not be set up on this browser."
      );
    }
  }

  const visibleRows = [
    ...(unitGroup ? [unitGroup] : []),
    ...customGroups,
    ...routingThreads,
    ...conversations,
  ];
  const inboxRows = visibleRows.filter((row) => !row.is_archived);
  const archivedRows = visibleRows.filter((row) => row.is_archived);
  const listedRows = [...(showArchived ? archivedRows : inboxRows)].sort(
    (a, b) => Number(Boolean(b.is_pinned)) - Number(Boolean(a.is_pinned))
  );
  const activeConversation =
    visibleRows.find((row) => row.id === activeId) || thread;
  const isUnitGroup = activeConversation?.kind === "unit_group";
  const isGroup = Boolean(
    activeConversation?.kind && activeConversation.kind !== "private"
  );
  const isCustomGroup = activeConversation?.kind === "custom_group";
  const isRoutingThread = activeConversation?.kind === "group_request";
  const peer = isGroup ? null : activeConversation?.peer;
  const typing = !isGroup && thread?.peer_typing;
  const canPost =
    !isGroup || (thread?.can_post ?? activeConversation?.can_post) !== false;
  // Until the card arrives, do not guess: a routing thread opened by id must
  // not briefly render as a private conversation with a composer.
  const waitingForThread = Boolean(activeId) && !activeConversation && threadLoading;

  return (
    <div className={`messaging ${activeId ? "is-thread-open" : ""}`}>
      <aside className="messaging-pane messaging-list" aria-label="Conversations">
        <header className="messaging-pane-head">
          <div>
            <h1>Messages</h1>
            <p className="messaging-sub">
              {privateEnabled ? "Unit chat and private messages" : "Unit chat"}
              {unreadTotal ? ` · ${unreadTotal} unread` : ""}
              {(archivedRows.length > 0 || showArchived) && (
                <button
                  type="button"
                  className="msg-link messaging-archive-link"
                  onClick={toggleArchived}
                >
                  {showArchived ? "Inbox" : `Archived (${archivedRows.length})`}
                </button>
              )}
            </p>
          </div>
          <div className="messaging-head-actions">
            {privateEnabled ? (
              <Button size="sm" onClick={() => setPeopleOpen((open) => !open)}>
                New
              </Button>
            ) : (
              <Badge tone="warning">Private off</Badge>
            )}
            {isOperationsCenter && (
              <Button
                size="sm"
                variant="primary"
                onClick={() => setGcOpen(true)}
                aria-label="Request a group chat"
                title="Request a new group chat"
              >
                <Icon name="plus" size={15} />
                GC
              </Button>
            )}
          </div>
        </header>

        <form className="messaging-search" onSubmit={runSearch} role="search">
          <Input
            value={globalTerm}
            onChange={(event) => setGlobalTerm(event.target.value)}
            placeholder="Search messages…"
            aria-label="Search messages"
          />
          <Button variant="ghost" size="sm" type="submit" disabled={searching}>
            <Icon name="eye" size={15} />
          </Button>
        </form>

        {selecting && (
          <div className="messaging-select-bar">
            <strong>{selectedIds.size} selected</strong>
            <div className="btn-row">
              <Button
                size="sm"
                variant="ghost"
                disabled={stateBusy}
                onClick={() => bulkAction(showArchived ? "unarchive" : "archive")}
              >
                {showArchived ? "Unarchive" : "Archive"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={stateBusy}
                onClick={() => bulkAction("mute")}
              >
                Mute
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={stateBusy}
                onClick={() => bulkAction("delete")}
              >
                Delete
              </Button>
              <Button size="sm" variant="ghost" onClick={cancelSelecting}>
                Cancel
              </Button>
            </div>
          </div>
        )}

        {notice && (
          <p className="messaging-note" role="status">
            {notice}
          </p>
        )}

        {!privateEnabled && (
          <p className="messaging-note" role="status">
            Private one-to-one messaging is turned off by an Administrator. Unit
            chat is still available.
          </p>
        )}

        {peopleOpen && privateEnabled && (
          <div className="messaging-people">
            <Input
              value={peopleTerm}
              onChange={(event) => setPeopleTerm(event.target.value)}
              placeholder="Name, serial number or unit"
              aria-label="Search personnel"
              autoFocus
            />
            {peopleLoading ? (
              <p className="messaging-note">Searching…</p>
            ) : people.length ? (
              <ul className="people-list">
                {people.map((person) => (
                  <li key={person.id}>
                    <button
                      type="button"
                      className="msg-person"
                      onClick={() => startWith(person)}
                    >
                      <span className="msg-avatar" aria-hidden="true">
                        {initials(person)}
                      </span>
                      <span className="msg-person-text">
                        <span className="msg-person-name">{person.full_name}</span>
                        <span className="msg-person-meta">
                          {[person.rank, person.serial_number, person.unit_acronym]
                            .filter(Boolean)
                            .join(" · ")}
                        </span>
                      </span>
                      <span
                        className={`msg-presence ${person.online ? "is-online" : ""}`}
                        aria-label={person.online ? "Online" : "Offline"}
                      />
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="messaging-note">No matching personnel.</p>
            )}
          </div>
        )}

        {searchResults ? (
          <div className="messaging-results">
            <header className="results-head">
              <strong>{searchResults.results.length} result(s)</strong>
              <button type="button" className="msg-link" onClick={() => setSearchResults(null)}>
                Clear
              </button>
            </header>
            {searchResults.results.length ? (
              <ul className="result-list">
                {searchResults.results.map((row) => (
                  <li key={row.id}>
                    <button
                      type="button"
                      className="result-row"
                      onClick={() => openConversation(row.conversation_id, searchResults.term)}
                    >
                      <span className="result-peer">
                        {row.peer?.full_name || row.title}
                      </span>
                      <span className="result-body">
                        {row.is_mine ? "You: " : ""}
                        {row.body}
                      </span>
                      <span className="result-when">{formatDate(row.created_at)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState message="No messages match that search." />
            )}
          </div>
        ) : listLoading && !visibleRows.length ? (
          <Loading label="Loading conversations…" />
        ) : listedRows.length ? (
          <ul className="conversation-list">
            {listedRows.map((row) => {
              const groupRow = row.kind !== "private";
              const selected = selectedIds.has(row.id);
              return (
                <li key={row.id}>
                  <button
                    type="button"
                    className={`conversation-item ${row.id === activeId ? "active" : ""}${
                      selected ? " is-selected" : ""
                    }`}
                    onClick={() => handleRowClick(row)}
                    onPointerDown={(event) => beginLongPress(row, event)}
                    onPointerMove={moveLongPress}
                    onPointerUp={endLongPress}
                    onPointerCancel={endLongPress}
                    onPointerLeave={endLongPress}
                    onContextMenu={(event) => {
                      event.preventDefault();
                      setMenuRow(row);
                    }}
                    aria-pressed={selecting ? selected : undefined}
                  >
                    {selecting && (
                      <span
                        className={`conv-select ${selected ? "is-on" : ""}`}
                        aria-hidden="true"
                      >
                        {selected ? <Icon name="check" size={12} /> : null}
                      </span>
                    )}
                    <span
                      className={`msg-avatar ${groupRow ? "msg-avatar-group" : ""}`}
                      aria-hidden="true"
                    >
                      {groupRow ? <Icon name="personnel" size={16} /> : initials(row.peer)}
                    </span>
                    <span className="conversation-text">
                      <span className="conversation-top">
                        <span className="conversation-name">
                          {groupRow ? row.title : row.peer.full_name}
                        </span>
                        {row.last_message?.created_at && (
                          <span className="conversation-when">
                            {formatTime(row.last_message.created_at)}
                          </span>
                        )}
                      </span>
                      <span className="conversation-preview">
                        {row.peer_typing ? (
                          <em>typing…</em>
                        ) : row.last_message ? (
                          <>
                            {row.last_message.is_mine ? "You: " : ""}
                            {row.last_message.body}
                          </>
                        ) : groupRow ? (
                          <em>
                            {row.kind === "unit_group"
                              ? "Unit channel"
                              : row.kind === "group_request"
                                ? "Group chat request"
                                : "Group chat"}{" "}
                            · {row.member_count} members
                          </em>
                        ) : (
                          <em>No messages yet</em>
                        )}
                      </span>
                    </span>
                    <span className="conversation-flags">
                      {row.is_pinned && (
                        <span className="conv-flag" title="Pinned" aria-label="Pinned">
                          <Icon name="pin" size={13} />
                        </span>
                      )}
                      {row.is_muted && (
                        <span className="conv-flag" title="Muted" aria-label="Muted">
                          <Icon name="mute" size={13} />
                        </span>
                      )}
                      {!groupRow && (
                        <span
                          className={`msg-presence ${row.peer.online ? "is-online" : ""}`}
                          aria-label={row.peer.online ? "Online" : "Offline"}
                        />
                      )}
                      {row.unread_count > 0 && (
                        <Badge tone="info">
                          {row.unread_count > 99 ? "99+" : row.unread_count}
                        </Badge>
                      )}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : (
          <EmptyState
            message={
              showArchived
                ? "No archived conversations."
                : privateEnabled
                  ? "No conversations yet. Choose New to start one."
                  : "No unit chat is available for your account."
            }
          />
        )}

        {error && <ErrorBanner message={error} onRetry={() => loadConversations()} />}
      </aside>

      {gcOpen && isOperationsCenter && (
        <GroupChatApprovals
          onClose={() => setGcOpen(false)}
          onCreated={(created) => {
            setGcOpen(false);
            loadConversations({ silent: true });
            if (created?.thread_id) openConversation(created.thread_id);
          }}
        />
      )}

      <section className="messaging-pane messaging-thread" aria-label="Conversation">
        {activeId && waitingForThread ? (
          <Loading label="Loading conversation…" />
        ) : activeId ? (
          <>
            <header className="thread-head">
              <button
                type="button"
                className="icon-btn messaging-back"
                onClick={closeThread}
                aria-label="Back to conversations"
              >
                ←
              </button>
              <span
                className={`msg-avatar ${isGroup ? "msg-avatar-group" : ""}`}
                aria-hidden="true"
              >
                {isGroup ? <Icon name="personnel" size={16} /> : initials(peer)}
              </span>
              <div className="thread-title">
                <strong>
                  {isGroup
                    ? thread?.title || activeConversation?.title || "Group chat"
                    : peer?.full_name || "Conversation"}
                </strong>
                <span className="thread-meta">
                  {isRoutingThread ? (
                    <>
                      Group chat request
                      {thread?.status_label ? ` · ${thread.status_label}` : ""}
                    </>
                  ) : isGroup ? (
                    <>
                      {thread?.member_count ?? activeConversation?.member_count ?? 0} members
                      {" · "}
                      {isCustomGroup ? "Group chat" : "Unit chat"}
                    </>
                  ) : typing ? (
                    <em>typing…</em>
                  ) : (
                    <>
                      {personMeta(peer)}
                      {peer && (
                        <span className={`msg-presence ${peer.online ? "is-online" : ""}`} />
                      )}
                      {peer?.online ? " online" : " offline"}
                    </>
                  )}
                </span>
              </div>
              {(isUnitGroup || isCustomGroup) && (
                <Button variant="ghost" size="sm" onClick={openMembers}>
                  Members
                </Button>
              )}
              {notificationsSupported() && permission !== "granted" && (
                <Button variant="ghost" size="sm" onClick={enableNotifications}>
                  Enable notifications
                </Button>
              )}
            </header>

            {notificationNotice && (
              <p className="messaging-note" role="status">
                {notificationNotice}
              </p>
            )}

            {membersOpen && (isUnitGroup || isCustomGroup) && (
              <div
                className="group-members"
                role="dialog"
                aria-label={isUnitGroup ? "Unit chat members" : "Group chat members"}
              >
                <header className="group-members-head">
                  <div>
                    <strong>{isUnitGroup ? "Unit chat members" : "Group chat members"}</strong>
                    <p className="hint">
                      {isUnitGroup
                        ? memberCanManage
                          ? "The Operations Center controls who can see and post in this unit channel. Administrators and the Operations Center always keep access."
                          : "Everyone in this unit who can see the channel, highest rank first. The Operations Center manages these permissions."
                        : "The personnel selected for this group and the approvers who admitted them can see it, highest rank first."}
                    </p>
                  </div>
                  <Button variant="ghost" size="sm" onClick={() => setMembersOpen(false)}>
                    Close
                  </Button>
                </header>
                {memberNotice && (
                  <p className="messaging-note" role="status">
                    {memberNotice}
                  </p>
                )}
                {membersLoading ? (
                  <Loading label={isUnitGroup ? "Loading unit roster…" : "Loading members…"} />
                ) : members.length ? (
                  <ul className="group-member-list">
                    {members.map((member) => (
                      <li className="group-member-row" key={member.id}>
                        <span className="group-member-name">
                          <strong>{member.full_name}</strong>
                          <span className="hint">
                            {[member.rank, member.serial_number].filter(Boolean).join(" · ")}
                            {member.is_admin ? " · Group admin" : ""}
                            {member.is_self ? " · You" : ""}
                          </span>
                        </span>
                        {isUnitGroup && memberCanManage && (
                          <>
                            <label className="check-row">
                              <input
                                type="checkbox"
                                checked={member.can_view}
                                disabled={member.is_admin || memberSavingId === member.id}
                                onChange={(event) =>
                                  updateMember(member, "can_view", event.target.checked)
                                }
                              />
                              View
                            </label>
                            <label className="check-row">
                              <input
                                type="checkbox"
                                checked={member.can_post}
                                disabled={member.is_admin || memberSavingId === member.id}
                                onChange={(event) =>
                                  updateMember(member, "can_post", event.target.checked)
                                }
                              />
                              Post
                            </label>
                          </>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <EmptyState
                    message={
                      isUnitGroup
                        ? "No active members in this unit."
                        : "No active members in this group."
                    }
                  />
                )}
              </div>
            )}

            {threadTerm && (
              <div className="thread-filter">
                Showing messages matching “{threadTerm}”
                <button type="button" className="msg-link" onClick={() => setThreadTerm("")}>
                  Clear
                </button>
              </div>
            )}

            <div
              className="thread-body"
              ref={bodyRef}
              onScroll={(event) => {
                const element = event.currentTarget;
                autoScroll.current =
                  element.scrollHeight - element.scrollTop - element.clientHeight < 80;
              }}
            >
              {hasMore && !threadTerm && (
                <div className="load-older">
                  <Button variant="ghost" size="sm" onClick={handleLoadOlder}>
                    Load earlier messages
                  </Button>
                </div>
              )}
              {threadLoading ? (
                <Loading label="Loading conversation…" />
              ) : messages.length ? (
                messages.map((message, index) => {
                  const previous = index ? messages[index - 1] : null;
                  const newDay =
                    !previous ||
                    formatDate(previous.created_at) !== formatDate(message.created_at);
                  return (
                    <div key={message.id} className="thread-item">
                      {newDay && (
                        <div className="thread-day">{formatDate(message.created_at)}</div>
                      )}
                      <div className={`bubble-row ${message.is_mine ? "mine" : "theirs"}`}>
                        {!message.is_mine && (
                          <span className="bubble-avatar" aria-hidden="true">
                            {initials(message.sender)}
                          </span>
                        )}
                        <div className="bubble-stack">
                        <div className={`bubble ${message.is_unsent ? "is-unsent" : ""}`}>
                          {isGroup && !message.is_mine && (
                            <span className="bubble-sender">
                              {message.sender.full_name}
                            </span>
                          )}
                          {message.reply_to && (
                            <div className="bubble-reply">
                              <strong>
                                {message.reply_to.sender?.full_name || "Unknown sender"}
                              </strong>
                              <span>
                                {message.reply_to.is_unsent
                                  ? "Message unsent"
                                  : message.reply_to.body}
                              </span>
                            </div>
                          )}
                          {message.is_unsent ? (
                            <p className="bubble-unsent">
                              <em>Message unsent</em>
                            </p>
                          ) : (
                            <p>{renderMentions(message.body, message.mentions)}</p>
                          )}
                        </div>
                        {/* The clock and actions sit outside the bubble, so
                            the box itself only carries the message. */}
                        <div className="bubble-foot">
                          <span className="bubble-meta">
                            {message.is_unsent
                              ? `${
                                  message.is_mine
                                    ? "You unsent this"
                                    : `${
                                        message.unsent_by?.full_name ||
                                        message.sender.full_name
                                      } unsent this`
                                } · ${formatTime(
                                  message.unsent_at || message.created_at
                                )}`
                              : `${formatTime(message.created_at)}${
                                  message.is_mine && message.status
                                    ? ` · ${receiptLabel(message)}`
                                    : ""
                                }`}
                          </span>
                          {!message.is_unsent && (canPost || message.can_unsend) && (
                            <div className="bubble-actions">
                              {canPost && (
                                <button
                                  type="button"
                                  className="icon-btn bubble-action"
                                  onClick={() => startReply(message)}
                                  aria-label="Reply to this message"
                                  title="Reply"
                                >
                                  <Icon name="reply" size={13} />
                                </button>
                              )}
                              {message.can_unsend && (
                                <button
                                  type="button"
                                  className="icon-btn bubble-action"
                                  onClick={() => unsendMessage(message)}
                                  aria-label="Unsend this message"
                                  title="Unsend"
                                >
                                  <Icon name="trash" size={13} />
                                </button>
                              )}
                            </div>
                          )}
                        </div>
                        </div>
                      </div>
                    </div>
                  );
                })
              ) : (
                <EmptyState
                  message={threadTerm ? "No messages match that term." : "Say hello."}
                />
              )}
            </div>

            {threadError && <ErrorBanner message={threadError} onRetry={() => loadThread(activeId, { term: threadTerm })} />}

            {isRoutingThread ? (
              <div className="gc-thread-bar">
                {thread?.can_approve ? (
                  <>
                    <span className="hint">
                      {thread.status === "pending_commander"
                        ? "This request needs the Unit Commander's decision."
                        : "This request needs an Administrator's final approval."}
                    </span>
                    <div className="gc-thread-actions">
                      <Button
                        variant="ghost"
                        onClick={() => decideRequest("reject")}
                        disabled={requestSaving}
                      >
                        Reject
                      </Button>
                      <Button onClick={() => decideRequest("approve")} disabled={requestSaving}>
                        {thread.status === "pending_commander"
                          ? "Approve and route to Administrator"
                          : "Give final approval"}
                      </Button>
                    </div>
                  </>
                ) : (
                  <>
                    <span className="hint">
                      {thread?.status === "approved"
                        ? "Approved. The group chat appears in the inbox for its selected personnel."
                        : thread?.status === "rejected"
                          ? "This request was rejected."
                          : "Waiting for the next approval in the chain."}
                    </span>
                    {thread?.status === "approved" &&
                      thread?.conversation_id &&
                      thread?.can_open && (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => openConversation(thread.conversation_id)}
                        >
                          Open group chat
                        </Button>
                      )}
                  </>
                )}
              </div>
            ) : (
              <>
                {!canPost && (
                  <p className="messaging-note" role="status">
                    {isCustomGroup
                      ? "The Administrator has turned off chat in this group. You can still read it."
                      : "The Operations Center has turned off your permission to post in the unit chat. You can still read it."}
                  </p>
                )}
                <form className="msg-composer" onSubmit={handleSend}>
                  {replyTo && (
                    <div className="composer-reply">
                      <div>
                        <strong>Replying to {replyTo.sender.full_name}</strong>
                        <span>{replyTo.body.slice(0, 120)}</span>
                      </div>
                      <button
                        type="button"
                        className="msg-link"
                        onClick={() => setReplyTo(null)}
                      >
                        Cancel
                      </button>
                    </div>
                  )}
                  {mentions.length > 0 && (
                    <div className="composer-mentions">
                      <span>Mentioning:</span>
                      {mentions.map((person) => (
                        <button
                          key={person.id}
                          type="button"
                          className="mention-chip"
                          onClick={() => removeMention(person.id)}
                          title="Remove this mention"
                        >
                          @{person.full_name} ×
                        </button>
                      ))}
                    </div>
                  )}
                  {mentionOpen && (
                    <div className="mention-picker" role="dialog" aria-label="Mention someone">
                      <Input
                        value={mentionTerm}
                        onChange={(event) => setMentionTerm(event.target.value)}
                        placeholder="Search people to mention…"
                        aria-label="Search people to mention"
                      />
                      {mentionLoading ? (
                        <p className="hint">Loading…</p>
                      ) : (
                        <ul>
                          {mentionPeople
                            .filter((person) => {
                              const term = mentionTerm.trim().toLowerCase();
                              if (!term) return true;
                              return [person.full_name, person.serial_number]
                                .filter(Boolean)
                                .some((value) => value.toLowerCase().includes(term));
                            })
                            .slice(0, 8)
                            .map((person) => (
                              <li key={person.id}>
                                <button
                                  type="button"
                                  className="msg-person"
                                  onClick={() => chooseMention(person)}
                                >
                                  @{person.full_name}
                                </button>
                              </li>
                            ))}
                        </ul>
                      )}
                      <button
                        type="button"
                        className="msg-link"
                        onClick={() => setMentionOpen(false)}
                      >
                        Close
                      </button>
                    </div>
                  )}
                  <textarea
                    className="input"
                    value={draft}
                    onChange={handleDraft}
                    onKeyDown={handleComposerKey}
                    rows={2}
                    maxLength={4000}
                    disabled={!canPost}
                    placeholder={
                      canPost
                        ? "Write a message… (Enter to send, Shift+Enter for a new line)"
                        : isCustomGroup
                          ? "Chat is turned off for this group."
                          : "Posting is turned off for your account."
                    }
                    aria-label="Message"
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={!canPost || activeConversation?.kind === "private"}
                    onClick={() => {
                      if (activeConversation?.kind === "private") return;
                      setMentionTerm("");
                      setMentionOpen((open) => !open);
                    }}
                    aria-label="Mention someone"
                    title="Mention someone"
                  >
                    @
                  </Button>
                  <Button type="submit" disabled={!canPost || !draft.trim() || sending}>
                    Send
                  </Button>
                </form>
              </>
            )}
          </>
        ) : (
          <div className="thread-empty">
            <Icon name="feedback" size={34} />
            <h2>Private messages</h2>
            <p>Select a conversation, or start one with New.</p>
          </div>
        )}
      </section>

      {menuRow &&
        createPortal(
          <div
            className="conv-menu-backdrop"
            role="presentation"
            onClick={() => {
              if (Date.now() - menuOpenedAt.current < 400) return;
              setMenuRow(null);
            }}
          >
            <div
              className="conv-menu"
              role="menu"
              aria-label={`Actions for ${conversationTitle(menuRow)}`}
              onClick={(event) => event.stopPropagation()}
            >
              <header className="conv-menu-head">
                <strong>{conversationTitle(menuRow)}</strong>
                <span className="hint">Only your view changes.</span>
              </header>
              <button
                type="button"
                role="menuitem"
                disabled={stateBusy}
                onClick={() =>
                  applyAction(
                    menuRow,
                    menuRow.unread_count > 0 ? "mark_read" : "mark_unread"
                  )
                }
              >
                {menuRow.unread_count > 0 ? "Mark as read" : "Mark as unread"}
              </button>
              <button
                type="button"
                role="menuitem"
                disabled={stateBusy}
                onClick={() =>
                  applyAction(menuRow, menuRow.is_pinned ? "unpin" : "pin")
                }
              >
                {menuRow.is_pinned ? "Unpin" : "Pin"}
              </button>
              <button
                type="button"
                role="menuitem"
                disabled={stateBusy}
                onClick={() =>
                  applyAction(menuRow, menuRow.is_muted ? "unmute" : "mute")
                }
              >
                {menuRow.is_muted ? "Unmute" : "Mute"}
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => startSelecting(menuRow)}
              >
                Select
              </button>
              <button
                type="button"
                role="menuitem"
                disabled={stateBusy}
                onClick={() =>
                  applyAction(
                    menuRow,
                    menuRow.is_archived ? "unarchive" : "archive"
                  )
                }
              >
                {menuRow.is_archived ? "Unarchive" : "Archive"}
              </button>
              <button
                type="button"
                role="menuitem"
                className="danger"
                onClick={() => deleteConversation(menuRow)}
              >
                Delete
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => setMenuRow(null)}
              >
                Cancel
              </button>
            </div>
          </div>,
          document.body
        )}
    </div>
  );
}

function conversationTitle(row) {
  if (!row) return "";
  return row.kind === "private"
    ? row.peer?.full_name || "Conversation"
    : row.title || "Group chat";
}
