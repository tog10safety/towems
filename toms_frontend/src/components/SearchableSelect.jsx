/**
 * A select whose options can be typed into.
 *
 * Native <select> elements cannot be filtered by typing, and the personnel
 * roster is long enough that scrolling it is slow. This control keeps the same
 * value contract - a single id as a string - but shows a text field while it is
 * open: the query matches the visible label (rank/name and serial number), the
 * arrow keys move through the matches, Enter chooses, Escape closes.
 *
 * Pass `search` to look the options up on the server instead of filtering the
 * `options` array locally. The function receives the trimmed query and resolves
 * to `{ value, label, hint }` rows; it is debounced while the list is open so
 * typing one name is one request.
 *
 * Options are `{ value, label, hint }`. The parent owns the value so a cleared
 * selection is a real unassignment, not a hidden stale id. `selectedOption`
 * lets a form show its saved value before the remote lookup has run.
 */

import { useEffect, useId, useMemo, useRef, useState } from "react";

function matches(option, query) {
  if (!query) return true;
  return `${option.label} ${option.hint ?? ""}`
    .toLowerCase()
    .includes(query.trim().toLowerCase());
}

export default function SearchableSelect({
  value,
  onChange,
  options = [],
  search,
  selectedOption = null,
  onSelectOption,
  placeholder = "Search name or serial…",
  disabled = false,
  error = false,
  ariaLabel,
  emptyMessage = "No matching personnel.",
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [remoteOptions, setRemoteOptions] = useState([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [picked, setPicked] = useState(null);
  const inputRef = useRef(null);
  const requestId = useRef(0);
  const listId = useId();
  const remote = typeof search === "function";

  useEffect(() => {
    if (!value) setPicked(null);
  }, [value]);

  useEffect(() => {
    if (!remote || !open) return undefined;
    const id = ++requestId.current;
    const trimmed = query.trim();
    setSearching(true);
    setSearchError("");
    const timer = setTimeout(
      async () => {
        try {
          const rows = await search(trimmed);
          if (id === requestId.current) setRemoteOptions(rows ?? []);
        } catch {
          if (id === requestId.current) {
            setRemoteOptions([]);
            setSearchError("Unable to search personnel. Keep typing to retry.");
          }
        } finally {
          if (id === requestId.current) setSearching(false);
        }
      },
      trimmed ? 250 : 0
    );
    return () => {
      clearTimeout(timer);
      if (requestId.current === id) requestId.current += 1;
    };
  }, [remote, search, open, query]);

  const candidates = remote ? remoteOptions : options;
  const selected =
    candidates.find((option) => String(option.value) === String(value)) ||
    options.find((option) => String(option.value) === String(value)) ||
    (picked && String(picked.value) === String(value) ? picked : null) ||
    (selectedOption && String(selectedOption.value) === String(value)
      ? selectedOption
      : null);
  const filtered = useMemo(
    () => (remote ? candidates : candidates.filter((option) => matches(option, query))),
    [remote, candidates, query]
  );
  const activeIndex = Math.min(active, Math.max(0, filtered.length - 1));
  const shown = open ? query : selected?.label ?? "";

  function openList() {
    if (disabled) return;
    setOpen(true);
    setQuery("");
    setActive(0);
  }

  function choose(option) {
    onChange(String(option.value));
    setPicked(option);
    onSelectOption?.(option);
    setQuery("");
    setOpen(false);
    inputRef.current?.blur();
  }

  function clear() {
    onChange("");
    setPicked(null);
    onSelectOption?.(null);
    setQuery("");
    setOpen(false);
  }

  function onKeyDown(event) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (!open) openList();
      else setActive(Math.min(filtered.length - 1, activeIndex + 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) openList();
      else setActive(Math.max(0, activeIndex - 1));
    } else if (event.key === "Enter") {
      if (!open) return;
      event.preventDefault();
      if (filtered[activeIndex]) choose(filtered[activeIndex]);
    } else if (event.key === "Escape") {
      if (!open) return;
      event.preventDefault();
      setOpen(false);
      setQuery("");
      inputRef.current?.blur();
    }
  }

  return (
    <div className="searchable">
      <input
        ref={inputRef}
        className={error ? "input invalid" : "input"}
        type="text"
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={
          open && filtered[activeIndex] ? `${listId}-${activeIndex}` : undefined
        }
        autoComplete="off"
        value={shown}
        placeholder={placeholder}
        disabled={disabled}
        onFocus={openList}
        onBlur={() => setOpen(false)}
        onChange={(event) => {
          setOpen(true);
          setQuery(event.target.value);
          setActive(0);
        }}
        onKeyDown={onKeyDown}
      />

      {selected && !disabled && (
        <button
          type="button"
          className="searchable-clear"
          aria-label={`Clear ${selected.label}`}
          onMouseDown={(event) => event.preventDefault()}
          onClick={clear}
        >
          ×
        </button>
      )}

      {open && (
        <div
          className="searchable-menu"
          id={listId}
          role="listbox"
          aria-busy={searching || undefined}
        >
          {searchError ? (
            <p className="searchable-empty">{searchError}</p>
          ) : filtered.length === 0 ? (
            <p className="searchable-empty">
              {searching ? "Searching…" : emptyMessage}
            </p>
          ) : (
            filtered.map((option, index) => (
              <button
                key={option.value}
                id={`${listId}-${index}`}
                type="button"
                role="option"
                aria-selected={String(option.value) === String(value)}
                className={`searchable-option ${index === activeIndex ? "active" : ""}`}
                onMouseDown={(event) => event.preventDefault()}
                onMouseEnter={() => setActive(index)}
                onClick={() => choose(option)}
              >
                <span>{option.label}</span>
                {option.hint && <span className="searchable-hint">{option.hint}</span>}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
