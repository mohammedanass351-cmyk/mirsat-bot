def extract_tags(text: str) -> list:
    """يستخرج كل الوسوم (#كلمة) من نص التعليق."""
    if not text:
        return []
    arabic_digits = "٠١٢٣٤٥٦٧٨٩"
    english_digits = "0123456789"
    text = text.translate(str.maketrans(arabic_digits, english_digits))
    return re.findall(r"#(\S+)", text)
