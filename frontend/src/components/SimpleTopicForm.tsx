// The everyday way to add a topic: pick an answer from the bank's FAQ, then say how customers ask it.
import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, Plus, Search, X } from "lucide-react";
import { useEffect, useState, type KeyboardEvent } from "react";
import { api, query } from "@/api/client";
import type { FaqConfig, FaqEntry, TopicDetail } from "@/api/types";
import { ErrorAlert, Field, Loading } from "@/components/ui";
import { faqTopic } from "@/lib/faq";

export function SimpleTopicForm({ config, topicId, initial, onDone }: {
  config: FaqConfig;
  topicId?: string;
  initial?: { key: string; title: string; examples: string[] };
  onDone: (t: TopicDetail) => void;
}) {
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<FaqEntry | null>(null);
  const [pickedKey, setPickedKey] = useState(initial?.key ?? "");
  const [title, setTitle] = useState(initial?.title ?? "");
  const [examples, setExamples] = useState<string[]>(initial?.examples ?? []);
  const [example, setExample] = useState("");

  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 250);
    return () => clearTimeout(t);
  }, [search]);

  const entries = useQuery({
    queryKey: ["faq-entries", q],
    queryFn: () => api<FaqEntry[]>(`/faq/entries${query({ q })}`),
    placeholderData: (prev) => prev,
  });
  const chosen = picked ?? entries.data?.find((e) => e.key === pickedKey) ?? null;

  function pick(entry: FaqEntry) {
    setPicked(entry);
    setPickedKey(entry.key);
    if (!title.trim() && entry.question) setTitle(entry.question);
    if (examples.length === 0 && entry.question) setExamples([entry.question]);
  }

  const addExample = () => {
    const v = example.trim();
    if (v.length >= 3 && !examples.includes(v)) setExamples([...examples, v]);
    setExample("");
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addExample();
    }
  };

  const save = useMutation({
    mutationFn: () => {
      const body = faqTopic(config, pickedKey, title.trim(), examples);
      return topicId
        ? api<TopicDetail>(`/topics/${topicId}`, { method: "PUT", json: body })
        : api<TopicDetail>("/topics", { json: body });
    },
    onSuccess: onDone,
  });

  return (
    <form
      className="steps"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <section className="card">
        <div className="step-head">
          <span className="step-num">1</span>
          <div>
            <h2>Pick the answer</h2>
            <p className="card-sub">Choose the answer from the bank’s FAQ that customers should receive.</p>
          </div>
        </div>
        <div className="stack">
          <label className="search-box" style={{ boxShadow: "none" }}>
            <Search size={18} aria-hidden />
            <input value={search} placeholder="Search the FAQ, e.g. statement, opening hours"
              aria-label="Search the FAQ" onChange={(e) => setSearch(e.target.value)} />
          </label>
          <ErrorAlert error={entries.error} />
          {entries.isLoading ? (
            <Loading />
          ) : (
            <div className="faq-list" role="radiogroup" aria-label="FAQ answers">
              {(entries.data ?? []).map((entry) => {
                const active = entry.key === pickedKey;
                return (
                  <button type="button" key={entry.key} role="radio" aria-checked={active}
                    className={`faq-entry ${active ? "active" : ""}`} onClick={() => pick(entry)}>
                    <span className="faq-entry-head">
                      <strong>{entry.question || entry.key}</strong>
                      {active && <CheckCircle2 size={18} aria-hidden />}
                    </span>
                    <span className="faq-entry-answer">{entry.answer}</span>
                  </button>
                );
              })}
              {entries.data?.length === 0 && <p className="card-sub">Nothing in the FAQ matches “{q}”.</p>}
            </div>
          )}
        </div>
      </section>

      <section className="card">
        <div className="step-head">
          <span className="step-num">2</span>
          <div>
            <h2>How do customers ask it?</h2>
            <p className="card-sub">Give the topic a name and a few different ways a customer might write the question.</p>
          </div>
        </div>
        <div className="stack">
          <Field label="Topic name">
            <input className="input" required maxLength={200} value={title} placeholder="e.g. Bank statements"
              onChange={(e) => setTitle(e.target.value)} />
          </Field>
          <Field label="Ways a customer might ask" hint="Three or more different phrasings work best. Press Enter to add each one.">
            <div className="row">
              <input className="input" style={{ flex: 1, minWidth: 200 }} value={example} maxLength={500}
                placeholder="e.g. Can you send me my statement?" onChange={(e) => setExample(e.target.value)} onKeyDown={onKey} />
              <button type="button" className="btn btn-ghost" onClick={addExample}>
                <Plus size={16} /> Add
              </button>
            </div>
          </Field>
          {examples.length > 0 && (
            <div className="chips">
              {examples.map((ex) => (
                <span className="chip" key={ex}>
                  {ex}
                  <button type="button" aria-label={`Remove ${ex}`} onClick={() => setExamples(examples.filter((x) => x !== ex))}>
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}
          {chosen && (
            <div>
              <div className="cell-sub" style={{ marginBottom: 6 }}>The customer will receive:</div>
              <div className="mail-body">{chosen.answer}</div>
            </div>
          )}
        </div>
      </section>

      <ErrorAlert error={save.error} />
      <div className="row">
        <span className="spacer" />
        <button className="btn btn-primary" disabled={save.isPending || !pickedKey || !title.trim() || examples.length === 0}>
          {save.isPending ? "Submitting…" : "Submit for approval"}
        </button>
      </div>
    </form>
  );
}
