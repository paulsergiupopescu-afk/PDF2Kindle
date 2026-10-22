# pdf2kindle – reguli pentru orice EPUB

## Note de subsol (regulă generală, valabilă pentru orice EPUB produs)

- Notele de subsol **nu** stau în capitol: nici la final de capitol, nici la
  piciorul paginii.
- **Toate** notele cărții merg pe o singură pagină „Note” la **sfârșitul
  cărții** (`notes.xhtml`, ultimul element din spine), grupate sub titlul
  capitolului din care provin.
- În text rămâne doar markerul, ca `noteref` EPUB3, ca să se deschidă ca
  **pop-up** pe Kindle; fiecare notă are link înapoi la marker.
- Regula se aplică și EPUB-urilor construite în afara convertorului (scripturi
  ad-hoc, alte surse): note la sfârșitul cărții, pop-up în text.
