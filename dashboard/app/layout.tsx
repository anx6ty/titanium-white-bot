import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Titanium White Dashboard",
  description: "Manage your Discord community with a clean, white dashboard."
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
