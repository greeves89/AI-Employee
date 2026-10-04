"""QR-Code als SVG — ohne Bild-Bibliothek.

Eine Stelle für alle QR-Codes der Plattform: das Sprach-Cockpit zeigt Links als
QR-Code (``show_on_screen kind='qr'``), die Zwei-Faktor-Einrichtung den
Schlüssel für die Authenticator-App (#915). ``qrcode`` liefert nur die
Bit-Matrix, das SVG bauen wir selbst — kein Pillow, kein Bild-Stack.
"""

from __future__ import annotations


def qr_svg(data: str, groesse: int = 320) -> str:
    """``data`` als QR-Code-SVG (schwarz auf weiß, mit Ruhezone)."""
    import qrcode

    qr = qrcode.QRCode(border=2, box_size=1)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    rects = [
        f'<rect x="{x}" y="{y}" width="1" height="1"/>'
        for y, row in enumerate(matrix) for x, cell in enumerate(row) if cell
    ]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" '
        f'shape-rendering="crispEdges" width="{groesse}" height="{groesse}">'
        f'<rect width="{n}" height="{n}" fill="#fff"/>'
        f'<g fill="#000">{"".join(rects)}</g></svg>'
    )
