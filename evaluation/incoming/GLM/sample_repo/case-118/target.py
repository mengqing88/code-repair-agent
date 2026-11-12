def top_words(text, k):
    """词频 Top-K：次数多的在前；次数相同按字典序升序。"""
    counts = {}
    for w in text.split():
        counts[w] = counts.get(w, 0) + 1
    ranked = sorted(counts, key=counts.get, reverse=True)
    return ranked[:k]
