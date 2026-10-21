def chunk_text(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    if size < 1 or overlap < 0 or overlap >= size:
        raise ValueError("chunk overlap must be non-negative and smaller than chunk size")
    step = size - overlap
    chunks: list[str] = []
    for start in range(0, len(text), step):
        piece = text[start : start + size]
        if piece.strip():
            chunks.append(piece)
        if start + size >= len(text):
            break
    return chunks
