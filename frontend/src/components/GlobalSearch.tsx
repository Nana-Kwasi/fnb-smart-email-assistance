import { useQuery } from "@tanstack/react-query";
import { BookOpenCheck, LayoutGrid, Library, Mail, Search, UserRound } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, query } from "@/api/client";
import type { SearchHit } from "@/api/types";

const GROUPS: { kind: SearchHit["kind"]; label: string; icon: JSX.Element }[] = [
  { kind: "email", label: "Emails", icon: <Mail size={16} /> },
  { kind: "document", label: "Knowledge base", icon: <Library size={16} /> },
  { kind: "topic", label: "Topics", icon: <BookOpenCheck size={16} /> },
  { kind: "user", label: "People", icon: <UserRound size={16} /> },
  { kind: "page", label: "Pages", icon: <LayoutGrid size={16} /> },
];

function useDebounced(value: string, ms = 200) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

/** Search emails (by reference, sender or subject), topics, people and pages.
 *  The server only returns what the signed-in person's role allows. Shortcut: Ctrl/⌘ + K. */
export function GlobalSearch() {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const q = useDebounced(text.trim());

  const { data, isFetching } = useQuery({
    queryKey: ["search", q],
    queryFn: () => api<{ hits: SearchHit[] }>(`/search${query({ q })}`),
    enabled: q.length >= 2,
    staleTime: 10_000,
  });

  const ordered = useMemo(
    () => GROUPS.flatMap((g) => (data?.hits ?? []).filter((h) => h.kind === g.kind)),
    [data],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        input.current?.focus();
        setOpen(true);
      }
    };
    const onClick = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, []);

  useEffect(() => setActive(0), [q]);

  function go(hit: SearchHit) {
    setOpen(false);
    setText("");
    input.current?.blur();
    navigate(hit.url);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, ordered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter" && ordered[active]) {
      go(ordered[active]);
    } else if (e.key === "Escape") {
      setOpen(false);
      input.current?.blur();
    }
  }

  const isMac = typeof navigator !== "undefined" && /Mac/i.test(navigator.platform);
  let index = -1;

  return (
    <div className="search" ref={wrap}>
      <label className="search-box">
        <Search size={18} aria-hidden />
        <input
          ref={input}
          value={text}
          placeholder="Search reference numbers, emails, topics…"
          aria-label="Search"
          onChange={(e) => {
            setText(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          maxLength={100}
        />
        <span className="kbd" aria-hidden>
          {isMac ? "⌘K" : "Ctrl K"}
        </span>
      </label>
      {open && q.length >= 2 && (
        <div className="search-results" role="listbox">
          {ordered.length === 0 ? (
            <div className="search-empty">{isFetching ? "Searching…" : `No results for “${q}”`}</div>
          ) : (
            GROUPS.map((g) => {
              const hits = ordered.filter((h) => h.kind === g.kind);
              if (!hits.length) return null;
              return (
                <div key={g.kind}>
                  <div className="search-group">{g.label}</div>
                  {hits.map((hit) => {
                    index += 1;
                    const i = index;
                    return (
                      <button
                        key={`${hit.kind}-${hit.id}`}
                        role="option"
                        aria-selected={i === active}
                        className={`search-hit ${i === active ? "active" : ""}`}
                        onMouseEnter={() => setActive(i)}
                        onClick={() => go(hit)}
                      >
                        {g.icon}
                        <span style={{ minWidth: 0 }}>
                          <div className="search-hit-title">{hit.title}</div>
                          <div className="search-hit-sub">{hit.subtitle}</div>
                        </span>
                      </button>
                    );
                  })}
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
