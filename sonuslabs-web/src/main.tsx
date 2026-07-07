import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Landing } from "./pages/Landing";
import { Create } from "./pages/Create";
import { Console } from "./pages/Console";
import { Login } from "./pages/Login";
import { Docs } from "./pages/Docs";
import { Admin } from "./pages/Admin";
import { Privacy, Terms } from "./pages/Legal";
import { AuthProvider, RequireAuth } from "./auth";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/login" element={<Login />} />
          {/* Docs are public — developers read before they sign up. */}
          <Route path="/docs" element={<Docs />} />
          <Route path="/docs/:section" element={<Docs />} />
          <Route path="/privacy" element={<Privacy />} />
          <Route path="/terms" element={<Terms />} />
          <Route path="/create" element={<RequireAuth><Create /></RequireAuth>} />
          <Route path="/console" element={<RequireAuth><Console /></RequireAuth>} />
          {/* Operator monitor: RequireAuth for login, the page itself + the
              backend both enforce the ADMIN_EMAILS allowlist. */}
          <Route path="/admin" element={<RequireAuth><Admin /></RequireAuth>} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>
);
