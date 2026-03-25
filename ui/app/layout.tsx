import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Synapse UI",
  description: "Realtime operator interface for Synapse autonomous agents.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
