"""Synthetic PDF bytes for technical checks only; not research evidence."""

from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject


def pdf_bytes(
    texts=("Alpha evidence on physical page one", "", "Omega on physical page three"),
    encrypted=False,
):
    writer = PdfWriter()
    writer.add_metadata({"/Title": "Synthetic PaperTrail test fixture"})
    for text in texts:
        page = writer.add_blank_page(width=595, height=842)
        if text:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            page[NameObject("/Resources")] = DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject(
                        {NameObject("/F1"): writer._add_object(font)}
                    )
                }
            )
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            content = StreamObject()
            content.set_data(f"BT /F1 16 Tf 50 760 Td ({escaped}) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(content)
    if encrypted:
        writer.encrypt("synthetic-fixture-password")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()
