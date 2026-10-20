import { FileSpreadsheet, FileText } from "lucide-react";
import { query } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";

/** PDF and Excel downloads of the list on screen, with the same filters. Plain links, so the
 *  browser downloads them with the signed-in session. */
export function ExportButtons({ params }: { params: Record<string, string | number | boolean | null | undefined> }) {
  const { can } = useAuth();
  if (!can("emails:export")) return null;
  const qs = query(params);
  return (
    <div className="row" style={{ gap: 8 }}>
      <a className="btn btn-ghost" href={`/api/emails/export.pdf${qs}`}>
        <FileText size={16} /> PDF
      </a>
      <a className="btn btn-ghost" href={`/api/emails/export.xlsx${qs}`}>
        <FileSpreadsheet size={16} /> Excel
      </a>
    </div>
  );
}
