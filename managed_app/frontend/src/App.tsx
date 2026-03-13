import { HealthCheck } from "./components/HealthCheck";
import "./App.css";

function App() {
  return (
    <div className="app">
      <header>
        <h1>Self-Evolving Software</h1>
        <p>Managed Application</p>
      </header>
      <main>
        <HealthCheck />
      </main>
    </div>
  );
}

export default App;
