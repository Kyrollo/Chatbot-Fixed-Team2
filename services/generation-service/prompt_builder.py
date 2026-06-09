from schemas import Citation


def build_messages(query: str, citations: list[Citation]) -> list[dict[str, str]]:
    context_parts: list[str] = []
    for idx, citation in enumerate(citations, start=1):
        location = f"doc={citation.document_id}"
        if citation.page is not None:
            location += f", page={citation.page}"
        context_parts.append(f"[{idx}] {location}\n{citation.text}")

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
