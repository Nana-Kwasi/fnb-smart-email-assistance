import type { FaqConfig, Revision, TopicInput } from "@/api/types";

/** The topic the simple form creates: "reply with the answer of FAQ entry <key>". */
export function faqTopic(config: FaqConfig, key: string, title: string, examples: string[]): TopicInput {
  return {
    title,
    examples,
    data_source: config.data_source,
    builder: {
      table: config.table,
      columns: [config.answer_column],
      conditions: [{ column: config.key_column, source: "fixed", value: key }],
      order_by: null,
      many: false,
    },
    query: null,
    extractors: {},
    max_rows: 1,
    reply_template: `{${config.answer_column}}`,
    row_template: null,
    require_verified_sender: false,
  };
}

/** The FAQ key a revision answers with, or null if it is not a simple FAQ topic. */
export function faqKeyOf(revision: Revision | null | undefined, config: FaqConfig | null | undefined): string | null {
  const b = revision?.builder;
  if (!revision || !b || !config) return null;
  const c = b.conditions[0];
  const simple =
    revision.data_source === config.data_source &&
    b.table === config.table &&
    b.columns.length === 1 &&
    b.columns[0] === config.answer_column &&
    b.conditions.length === 1 &&
    c.source === "fixed" &&
    c.column === config.key_column &&
    revision.reply_template.trim() === `{${config.answer_column}}`;
  return simple ? String(c.value ?? "") : null;
}
