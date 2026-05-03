import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Job-CV Matching Agent",
  description: "Upload CVs, paste a job description, and find the best match.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
