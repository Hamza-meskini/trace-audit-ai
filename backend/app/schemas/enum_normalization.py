"""Conservative spelling repair for controlled model-output vocabularies."""


def coerce_enum(raw: str, choices: tuple[str, ...]) -> str | None:
    text = str(raw or "").strip().upper()
    if text in choices:
        return text
    if not text:
        return None

    def one_edit(left: str, right: str) -> bool:
        if abs(len(left) - len(right)) > 1:
            return False
        if len(left) == len(right):
            return sum(a != b for a, b in zip(left, right)) == 1
        if len(left) > len(right):
            left, right = right, left
        for index, (a, b) in enumerate(zip(left, right)):
            if a != b:
                return left[index:] == right[index + 1:]
        return True

    matches = [choice for choice in choices if one_edit(text, choice)]
    return matches[0] if len(matches) == 1 else None
