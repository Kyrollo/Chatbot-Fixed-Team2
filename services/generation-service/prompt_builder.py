from schemas import Citation


def build_messages(query: str, citations: list[Citation]) -> list[dict[str, str]]:
    context_parts: list[str] = []
    for idx, citation in enumerate(citations, start=1):
        # Use filename when available, fall back to document_id
        doc_label = citation.filename if citation.filename else citation.document_id
        location = f"file={doc_label}"
        
        text = citation.text
        sheet_name = None
        if citation.source_type in ("xls", "xlsx") and text.startswith("Sheet:"):
            first_line_end = text.find("\n")
            if first_line_end != -1:
                sheet_name = text[6:first_line_end].strip()

        if citation.source_type == "csv" and citation.page is not None:
            row_start = (citation.page - 1) * 25 + 1
            row_end = citation.page * 25
            location += f", rows={row_start}-{row_end}"
        elif citation.source_type in ("xls", "xlsx") and citation.page is not None:
            row_start = (citation.page - 1) * 25 + 1
            row_end = citation.page * 25
            if sheet_name:
                location += f", sheet={sheet_name}"
            location += f", rows={row_start}-{row_end}"
        elif citation.page is not None:
            location += f", page={citation.page}"
            
        context_parts.append(f"[{idx}] {location}\n{text}")

    context_block = "\n\n".join(context_parts) if context_parts else "No supporting context retrieved."

    system_prompt = (
        "You are a retrieval-augmented assistant. Use the provided context first, "
        "answer concisely, and say when the context is insufficient. Do not invent citations."
    )
    user_prompt = (
        f"Question:\n{query}\n\n"
        f"Retrieved context:\n{context_block}\n\n"
        "Answer the question using only relevant context. Mention uncertainty when needed."
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
