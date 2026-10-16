import { useState } from "react";
import { Icon } from "./Icons.jsx";

export default function LoginGate({ onSubmit }) {
  const [token, setToken] = useState("");
  return (
    <div className="login">
      <form
        className="modal"
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit(token.trim());
        }}
      >
        <div className="brand-mark large">
          <Icon name="lock" size={22} />
        </div>
        <h2>Access token required</h2>
        <p className="muted">This RepoMind server is protected. Enter the APP_ACCESS_TOKEN it was started with.</p>
        <label className="field">
          <span>Access token</span>
          <input type="password" autoFocus value={token} onChange={(e) => setToken(e.target.value)} required />
        </label>
        <button className="btn primary wide">Continue</button>
      </form>
    </div>
  );
}
