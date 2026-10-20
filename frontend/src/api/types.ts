// Shapes returned by the API (mirrors backend/app/schemas).

export type Permission =
  | "overview:view"
  | "queue:view"
  | "queue:handle"
  | "emails:view"
  | "emails:export"
  | "knowledge:view"
  | "knowledge:edit"
  | "knowledge:publish"
  | "topics:view"
  | "topics:edit"
  | "topics:approve"
  | "test:use"
  | "test:run-queries"
  | "datasources:manage"
  | "settings:view"
  | "settings:edit"
  | "users:view"
  | "users:manage"
  | "audit:view"
  | "replies:send"
  | "replies:approve"
  | "cases:view"
  | "cases:handle"
  | "actions:approve"
  | "ai:view"
  | "ai:manage"
  | "analytics:view"
  | "knowledge:restricted";

export type Role = "officer" | "supervisor" | "auditor" | "admin";

export interface User {
  id: string;
  username: string;
  display_name: string;
  email: string | null;
  role: Role;
}

export interface Features {
  customer_data_answers: boolean;
  max_data_sources: number | null;
  require_second_person: boolean;
  max_upload_mb: number;
}

export interface Me {
  user: User & { previous_login_at: string | null };
  features: Features;
  permissions: Permission[];
  csrf_token: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export type EmailStatus = "received" | "queued" | "auto_answered" | "replied" | "closed" | "skipped";

export interface EmailSummary {
  id: string;
  reference: string;
  from_address: string;
  from_name: string;
  subject: string;
  received_at: string;
  status: EmailStatus;
  status_reason: string | null;
  reason_code: string | null;
  priority: "normal" | "high";
  ack_status: string;
  match_score: number | null;
  is_follow_up: boolean;
  assigned_to: User | null;
  urgent_alert_at: string | null;
  overdue_alert_at: string | null;
  handled_via: "automatic" | "outlook" | "dashboard" | null;
  handled_at: string | null;
  category: string | null;
  urgency: string | null;
  language: string | null;
  department: string | null;
  case_id: string | null;
}

export interface Reply {
  id: string;
  kind: "acknowledgement" | "auto_answer" | "ai_answer" | "officer";
  body_text: string;
  succeeded: boolean;
  error: string | null;
  created_at: string;
  sent_by: User | null;
  draft_id: string | null;
}

export interface Citation {
  n: number;
  document: string;
  document_id?: string;
  version: string;
  title: string;
  score: number;
}

export interface Check {
  check: string;
  passed: boolean;
  detail: string;
}

export interface Draft {
  id: string;
  status: "proposed" | "edited" | "pending_approval" | "sent" | "rejected" | "superseded";
  body_text: string;
  edited_text: string | null;
  citations: Citation[];
  validation: Check[];
  passed: boolean;
  model: string;
  policy_version: number;
  created_at: string;
  submitted_by: User | null;
  submitted_at: string | null;
  decided_by: User | null;
  decided_at: string | null;
  reject_reason: string | null;
}

export interface Attachment {
  id: string;
  name: string;
  content_type: string;
  size_bytes: number;
  scan_status: "clean" | "infected" | "not_scanned" | "error" | "rejected";
  scan_detail: string | null;
  extracted_text: string | null;
  problems: string[];
}

export interface DecisionRule {
  rule: string;
  ok: boolean;
  detail: string;
}

export interface Classification {
  subcategory_evidence: string[];
  next_steps: string[];
  needs_identity_verification: boolean;
  needs_staff_action: boolean;
  prompt_injection: string[];
  urgent_signals: string[];
  model_error: string | null;
}

export interface EmailDetail extends EmailSummary {
  body_text: string;
  web_link: string | null;
  sender_verified: boolean;
  handled_by: User | null;
  handled_note: string | null;
  replies: Reply[];
  suggested: { topic_id: string; title: string } | null;
  knowledge: KnowledgeUsed | null;
  intent: string | null;
  risk: string | null;
  sentiment: string | null;
  ai_summary: string | null;
  classification: Classification | null;
  classified_by: string | null;
  decision: { route: string; reason: string; rules: DecisionRule[]; mode: string; policy_version: number } | null;
  attachments: Attachment[];
  drafts: Draft[];
  case: {
    id: string;
    reference: string;
    status: string;
    emails: number;
    reopened_count: number;
    sla_due_at: string | null;
    possible_duplicate_of: string | null;
  } | null;
  approval: "officer" | "supervisor";
}

export interface KnowledgeUsed {
  title: string;
  content: string;
  document_id: string;
  document: string;
  category: string;
  version: string;
}

export interface ReasonCount {
  code: string;
  label: string;
  count: number;
}

export interface Attention {
  page: Page<EmailSummary>;
  reasons: ReasonCount[];
  overdue: number;
  overdue_after_hours: number;
}

export interface Condition {
  column: string;
  source: "fixed" | "sender_email" | "email_value";
  value?: string | null;
  value_kind?: "account_number" | "phone_number" | "card_last4" | "custom" | null;
  pattern?: string | null;
  label?: string | null;
}

export interface Builder {
  table: string;
  columns: string[];
  conditions: Condition[];
  order_by: { column: string; direction: "asc" | "desc" } | null;
  many: boolean;
}

export interface Revision {
  id: string;
  title: string;
  examples: string[];
  data_source: string;
  builder: Builder | null;
  query: string | null;
  extractors: Record<string, string>;
  max_rows: number;
  reply_template: string;
  row_template: string | null;
  require_verified_sender: boolean;
  status: "pending" | "approved" | "rejected" | "superseded";
  submitted_by: User;
  submitted_at: string;
  reviewed_by: User | null;
  reviewed_at: string | null;
  review_note: string | null;
}

export interface TopicSummary {
  id: string;
  title: string;
  data_source: string;
  is_active: boolean;
  is_live: boolean;
  has_pending: boolean;
  example_count: number;
  updated_at: string;
  answered_7d: number;
}

export interface TopicDetail {
  id: string;
  is_active: boolean;
  live: Revision | null;
  pending: Revision | null;
  history: Revision[];
}

export type TopicInput = Pick<
  Revision,
  | "title"
  | "examples"
  | "data_source"
  | "builder"
  | "query"
  | "extractors"
  | "max_rows"
  | "reply_template"
  | "row_template"
  | "require_verified_sender"
>;

export interface DataSourceName {
  name: string;
  kind: string;
}

export interface DataSource {
  id: string;
  name: string;
  kind: "oracle" | "sqlserver" | "postgresql" | "mysql" | "sqlite";
  host: string;
  port: number | null;
  database: string;
  db_schema: string | null;
  username: string;
  has_password: boolean;
  is_active: boolean;
  last_check_ok: boolean | null;
  last_check_at: string | null;
  last_error: string | null;
  updated_at: string;
}

export interface Column {
  name: string;
  type: string;
}

export interface Scored {
  topic_id: string;
  title: string;
  score: number;
  best_example: string;
  best_sentence: string;
}

export interface TestResult {
  would_auto_answer: boolean;
  decision: string;
  sensitive_keywords: string[];
  matched: Scored | null;
  ranking: Scored[];
  acknowledgement_preview: string;
  answer_preview: string | null;
  answer_reason: string | null;
}

export interface AppSettings {
  ack_enabled: boolean;
  auto_reply_enabled: boolean;
  confidence_threshold: number;
  ack_template: string;
  answer_header: string;
  answer_footer: string;
  sensitive_keywords: string[];
  suggest_threshold: number;
  knowledge_categories: Category[];
  escalation_to: string | null;
  escalation_copy_admins: boolean;
  max_upload_mb: number;
  require_second_person: boolean;
  max_auto_answer_chars: number;
  follow_up_days: number;
  page_size: number;
  session_idle_minutes: number;
  retention_months: number;
  notify_recipients: string[];
  notify_urgent: boolean;
  notify_overdue: boolean;
  overdue_after_hours: number;
  notify_digest: boolean;
  digest_time: string;
  notify_system: boolean;
  updated_at: string;
  mailbox: string;
  graph_configured: boolean;
  internal_domains: string[];
}

export interface Kpi {
  value: number | null;
  previous: number | null;
  spark: number[];
}

export interface Overview {
  briefing: string[];
  received: Kpi;
  auto_rate: Kpi;
  waiting: Kpi;
  avg_ack_seconds: Kpi;
  waiting_high_priority: number;
  overdue: number;
  overdue_after_hours: number;
  trend: { date: string; received: number; auto: number; people: number }[];
  reasons_7d: ReasonCount[];
  top_topics_7d: { title: string; count: number }[];
  topic_gaps: { title: string; count: number }[];
  recent: {
    id: string;
    reference: string;
    from_name: string;
    subject: string;
    received_at: string;
    status: EmailStatus;
    priority: "normal" | "high";
  }[];
  health: { name: string; ok: boolean | null; detail: string }[];
  pending_approvals: number;
  auto_reply_enabled: boolean;
  operations: Operations | null;
  automation_mode: string | null;
  kill_switch: boolean;
}

export interface Operations {
  incoming: number;
  acknowledged: number;
  ai_drafted: number;
  auto_resolved: number;
  answered_by_people: number;
  awaiting_review: number;
  urgent_escalations: number;
  avg_first_response_minutes: number | null;
  avg_resolution_hours: number | null;
  automation_rate: number | null;
  open_cases: number;
  sla_breached: number;
  definitions: Record<string, string>;
}

export interface CaseSummary {
  id: string;
  reference: string;
  customer_address: string;
  customer_name: string;
  sender_verified: boolean;
  status: "open" | "resolved" | "closed";
  category: string | null;
  category_label: string | null;
  department: string | null;
  priority: "normal" | "high";
  assigned_to: User | null;
  opened_at: string;
  sla_due_at: string | null;
  sla_breached: boolean;
  first_response_at: string | null;
  resolved_at: string | null;
  last_activity_at: string;
  reopened_count: number;
  emails: number;
  possible_duplicate_of: string | null;
}

export interface CaseEvent {
  id: string;
  at: string;
  kind: string;
  actor_name: string;
  email_id: string | null;
  text: string | null;
  detail: Record<string, unknown>;
}

export interface CaseActionRow {
  id: string;
  action: string;
  label: string;
  params: Record<string, unknown>;
  status: "pending_approval" | "done" | "rejected" | "failed";
  result: string | null;
  requested_by: User;
  decided_by: User | null;
  created_at: string;
  decided_at: string | null;
}

export interface CaseDetail extends CaseSummary {
  email_list: EmailSummary[];
  events: CaseEvent[];
  actions: CaseActionRow[];
  departments: string[];
}

export interface ActionSpec {
  name: string;
  label: string;
  description: string;
  needs_approval: boolean;
}

export interface CategoryPolicy {
  code: string;
  label: string;
  department: string;
  ack: boolean;
  auto_send: boolean;
  approval: "officer" | "supervisor";
  risk_tier: "low" | "medium" | "high";
  min_evidence: number;
  route: "queue" | "urgent";
  sla_hours: number;
  max_send_attempts: number;
  paused: boolean;
  paused_reason: string | null;
}

export interface AutomationPolicy {
  mode: "draft_only" | "supervised" | "restricted" | "expanded";
  mode_label: string;
  kill_switch: boolean;
  kill_switch_reason: string | null;
  categories: CategoryPolicy[];
  auto_languages: string[];
  ack_internal_senders: boolean;
  restricted_daily_cap: number;
  auto_pause_enabled: boolean;
  auto_pause_min_samples: number;
  auto_pause_reopen_rate: number;
  version: number;
  updated_at: string;
  departments: string[];
  modes: { value: string; label: string }[];
}

export interface QualityMetrics {
  days: number;
  categories: Record<string, Record<string, number | null>>;
  possible_missed_escalations: string[];
  drafts: {
    created: number;
    passed_checks: number;
    validation_failure_rate: number | null;
    failed_checks: Record<string, number>;
    sent_by_officers: number;
    edited_by_officers: number;
    officer_edit_rate: number | null;
    rejected: number;
  };
  ai_answers_sent: number;
  citation_coverage: number | null;
  models: Record<string, { calls: number; errors: number; error_rate: number | null; latency_p50_ms: number | null; latency_p95_ms: number | null }>;
}

export interface AiStatus {
  model: {
    enabled: boolean;
    server: string | null;
    fast_model: string | null;
    drafting_model: string | null;
    healthy: boolean;
    health_detail: string;
    timeout_seconds: number;
    max_concurrency: number;
    external_allowed: boolean;
  };
  attachment_scanning: boolean;
  policy: AutomationPolicy;
  quality: QualityMetrics;
  evaluations: {
    id: string;
    at: string;
    dataset: string;
    models: { fast: string | null; strong: string | null };
    cases: number;
    policy_version: number;
    metrics: Record<string, unknown>;
    run_by: string;
    ok: boolean;
  }[];
  changes: { at: string; by: string; version: number; reason: string; changes: Record<string, { before: unknown; after: unknown }> }[];
  incidents: { at: string; kind: string; subject: string; succeeded: boolean }[];
}

export interface Category {
  name: string;
  auto: boolean;
}

export interface SearchHit {
  kind: "email" | "document" | "topic" | "user" | "page";
  id: string;
  title: string;
  subtitle: string;
  url: string;
}

export interface NotificationEntry {
  id: string;
  kind: "urgent" | "overdue" | "digest" | "system";
  subject: string;
  recipients: string[];
  email_count: number;
  succeeded: boolean;
  error: string | null;
  sent_at: string;
}

export interface AdminUser extends User {
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface AuditEvent {
  id: string;
  at: string;
  actor_username: string;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  detail: Record<string, unknown>;
  ip_address: string | null;
}

export interface FaqConfig {
  data_source: string;
  table: string;
  key_column: string;
  question_column: string | null;
  answer_column: string;
}

export interface FaqEntry {
  key: string;
  question: string | null;
  answer: string;
}

export type VersionStatus = "draft" | "published" | "archived";

export interface KnowledgeVersion {
  id: string;
  version_label: string;
  status: VersionStatus;
  filename: string;
  size_bytes: number;
  effective_from: string | null;
  expires_on: string | null;
  in_date: boolean;
  notes: string | null;
  warnings: string[];
  entry_count: number;
  answered: number;
  suggested: number;
  uploaded_by: User;
  uploaded_at: string;
  published_by: User | null;
  published_at: string | null;
}

export interface KnowledgeSummary {
  id: string;
  name: string;
  category: string;
  audience: "public" | "internal" | "restricted";
  status: VersionStatus;
  live_version: string | null;
  has_draft: boolean;
  entry_count: number;
  answered: number;
  updated_at: string;
}

export interface KnowledgeDetail {
  id: string;
  name: string;
  category: string;
  audience: "public" | "internal" | "restricted";
  versions: KnowledgeVersion[];
}

export interface KnowledgeEntry {
  id: string;
  kind: "qa" | "section";
  title: string;
  content: string;
}

export interface TryResult {
  outcome: "automatic" | "suggested" | "person";
  explanation: string;
  hits: {
    title: string;
    content: string;
    document: string;
    document_id: string;
    version: string;
    version_status: VersionStatus;
    score: number;
  }[];
}

export interface KnowledgeHealth {
  documents_in_use: number;
  entries_in_use: number;
  index: string;
  expired: { id: string; name: string; version: string | null; expires_on: string | null; problem: string }[];
  expiring_soon: { id: string; name: string; version: string | null; expires_on: string | null; problem: string }[];
  drafts_waiting: { id: string; name: string; version: string | null; expires_on: string | null; problem: string }[];
  conflicts: string[];
}
