import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DataForge",
  description: "Turn an ML goal into a ready-to-train dataset.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
