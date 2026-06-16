from schemas import Citation


def build_messages(query: str, citations: list[Citation]) -> list[dict[str, str]]:
    context_parts: list[str] = []
    has_table_context = False

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

        # Detect table context
        if citation.chunk_type in ("table_nl", "table_md") or \
           "[TABLE_NL]" in text or "[TABLE_MD]" in text or "[TABLE]" in text:
            has_table_context = True

        # Strip internal markers before sending to LLM
        clean_text = text
        for marker in ("[TABLE_NL]", "[/TABLE_NL]", "[TABLE_MD]", "[/TABLE_MD]",
                       "[TABLE]", "[/TABLE]"):
            clean_text = clean_text.replace(marker, "")
        clean_text = clean_text.strip()
            
        context_parts.append(f"[{idx}] {location}\n{clean_text}")

    context_block = "\n\n".join(context_parts) if context_parts else "No supporting context retrieved."

    system_prompt = (
        "You are a retrieval-augmented assistant. Use the provided context first, "
        "answer concisely, and say when the context is insufficient. Do not invent citations."
    )

    # Add table-specific instructions when table context is present
    if has_table_context:
        system_prompt += (
            "\n\nSome context includes structured table data. When answering questions "
            "about table values, carefully match the row header AND column header "
            "to find the exact cell value. Do not approximate or interpolate. "
            "Pay close attention to the exact row and column labels in the query."
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
