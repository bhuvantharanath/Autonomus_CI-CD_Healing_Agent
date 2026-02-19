import sys
try:
    import pypdf
    with open(sys.argv[1], "rb") as f:
        reader = pypdf.PdfReader(f)
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
        print(text)
except ImportError:
    try:
        import PyPDF2
        with open(sys.argv[1], "rb") as f:
            reader = PyPDF2.PdfReader(f)
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
            print(text)
    except ImportError:
        print("Please install pypdf or PyPDF2")
