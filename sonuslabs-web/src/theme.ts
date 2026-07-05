// Design tokens lifted verbatim from the approved SonusLabs mockup.
export const C = {
  paper: "#FAF7F0",
  paperCard: "#FFFDF8",
  ink: "#211C15",
  inkSoft: "#5C5446",
  muted: "#6F6656",
  faint: "#8B8271",
  line: "#EBE3D3",
  lineSoft: "#E4DBC9",
  accent: "#E08A1E",
  accentDeep: "#B26A10",
  accentSoft: "#FBEBD2",
  accentSoftBorder: "#F1D9AC",
  green: "#1E8E78",
  // console (dark)
  dark: "#17140F",
  darkCard: "#1F1B14",
  darkLine: "#2A251C",
  darkText: "#EDE7DB",
  darkMuted: "#8A806C",
  red: "#F26C4F",
} as const;

export const serif = "'Instrument Serif',serif";
export const mono = "'JetBrains Mono',monospace";

// Real backend languages (all 11 Sarvam supports), display + native + font.
export const LANGS: { v: string; en: string; native: string; font: string }[] = [
  { v: "en-IN", en: "English", native: "English", font: serif },
  { v: "hi-IN", en: "Hindi", native: "हिंदी", font: "'Noto Serif Devanagari',serif" },
  { v: "kn-IN", en: "Kannada", native: "ಕನ್ನಡ", font: "'Noto Sans Kannada',sans-serif" },
  { v: "te-IN", en: "Telugu", native: "తెలుగు", font: "'Noto Sans Telugu',sans-serif" },
  { v: "ta-IN", en: "Tamil", native: "தமிழ்", font: "'Noto Sans Tamil',sans-serif" },
  { v: "ml-IN", en: "Malayalam", native: "മലയാളം", font: "system-ui" },
  { v: "mr-IN", en: "Marathi", native: "मराठी", font: "'Noto Serif Devanagari',serif" },
  { v: "bn-IN", en: "Bengali", native: "বাংলা", font: "system-ui" },
  { v: "gu-IN", en: "Gujarati", native: "ગુજરાતી", font: "system-ui" },
  { v: "pa-IN", en: "Punjabi", native: "ਪੰਜਾਬੀ", font: "system-ui" },
  { v: "od-IN", en: "Odia", native: "ଓଡ଼ିଆ", font: "system-ui" },
];

export const langLabel = (v: string) =>
  LANGS.find((l) => l.v === v)?.native ?? v;
