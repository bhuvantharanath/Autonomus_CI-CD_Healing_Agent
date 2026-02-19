import type { Fix } from "../../store/useRunStore";
import { CyberWidget } from "../CyberWidget";
import { Check, X } from "lucide-react";

export default function FixesTableWidget({ fixes }: { fixes: Fix[] }) {
    if (!fixes || fixes.length === 0) return null;

    return (
        <CyberWidget colorTheme="grey" headerTitle="FIX DB" footerCode="PATCH MATRIX" headerCode="LNK/01" className="h-full flex flex-col">
            <div className="overflow-auto custom-scrollbar flex-1 min-h-0">
                <table className="w-full text-left font-mono text-sm border-collapse">
                    <thead>
                        <tr className="border-b-2 border-black/30 font-tech text-xs uppercase tracking-wider bg-black/5">
                            <th className="p-3">File</th>
                            <th className="p-3">Bug Type</th>
                            <th className="p-3">Line Number</th>
                            <th className="p-3">Commit Message</th>
                            <th className="p-3">Status</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-black/10">
                        {fixes.map((fix, idx) => (
                            <tr
                                key={idx}
                                className="hover:bg-black/5 transition-colors group"
                            >
                                <td className="p-3">
                                    <div className="font-bold truncate max-w-[200px]" title={fix.file}>
                                        {fix.file.split('/').pop()}
                                    </div>
                                </td>
                                <td className="p-3">
                                    <span className="bg-black/10 px-2 py-1 rounded-sm text-xs font-bold group-hover:bg-black group-hover:text-(--color-cyber-grey) transition-colors">
                                        {fix.bug_type}
                                    </span>
                                </td>
                                <td className="p-3">
                                    <span className="opacity-70 group-hover:opacity-100">L{fix.line ?? '?'}</span>
                                </td>
                                <td className="p-3 truncate max-w-[250px]" title={fix.commit_message}>
                                    {fix.commit_message}
                                </td>
                                <td className="p-3 font-bold">
                                    {["FIXED","RESOLVED","SUCCESS","PASSED","verified","applied"].includes(fix.status) ? (
                                        <span className="text-green-600 flex items-center gap-1 bg-green-500/10 px-2 py-1 border border-green-600/30 rounded-xs w-fit">
                                            <Check size={14} /> Fixed
                                        </span>
                                    ) : (
                                        <span className="text-red-500 flex items-center gap-1 bg-red-500/10 px-2 py-1 border border-red-500/30 rounded-xs w-fit">
                                            <X size={14} /> Failed
                                        </span>
                                    )}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </CyberWidget>
    );
}
