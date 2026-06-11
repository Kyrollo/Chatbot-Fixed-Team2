import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "react-hot-toast";
import { AuthProvider } from "@/lib/auth.jsx";
import ChatPage from "@/pages/ChatPage.jsx";

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Toaster
          position="bottom-right"
          toastOptions={{
            style: {
              background: "#1C1E28",
              color: "#F0F2FF",
              border: "1px solid #2E3247",
              fontSize: "13px",
            },
            success: { iconTheme: { primary: "#2DD4C4", secondary: "#1C1E28" } },
            error:   { iconTheme: { primary: "#FF5C6A", secondary: "#1C1E28" } },
          }}
        />
        <Routes>
          <Route path="/" element={<ChatPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
