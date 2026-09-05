export interface ConvertResult {
  id: string;
  filename: string;
  title: string;
  author: string;
  pages: number;
  chapters: number;
  footnotes: number;
  images: number;
  ocr_pages: number;
  warnings: string[];
  download_url: string;
}

export interface ConvertOptions {
  title: string;
  author: string;
  lang: string;
  ocr: "auto" | "force" | "never";
  ocr_lang: string;
}
