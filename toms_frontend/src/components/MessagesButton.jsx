import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { api } from "../api/client.js";
import Icon from "./Icon.jsx";

/**
 * Unread private messages, in the top bar beside Online.
 *
 * Messages keep their own counter and their own page; the notification bell is
 * for system information and actions, so the two never share a badge.
 */
export default function MessagesButton() {
  const [unread, setUnread] = useState(0);

  useEffect(() => {
    let cancelled = false;
    async function loadUnread() {
      try {
        const data = await api.get("/messaging/summary/");
        if (!cancelled) setUnread(data.unread_total || 0);
      } catch {
        // The badge must never break the shell.
      }
    }
    loadUnread();
    const timer = setInterval(() => {
      if (!document.hidden) loadUnread();
    }, 30000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <NavLink
      className="icon-btn bell-btn"
      to="/messages"
      aria-label={`Messages: ${unread} unread`}
      title="Messages"
    >
      <Icon name="feedback" size={19} />
      {unread > 0 && (
        <span className="bell-count">{unread > 99 ? "99+" : unread}</span>
      )}
    </NavLink>
  );
}
