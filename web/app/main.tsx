import React from "react";
import {createRoot} from "react-dom/client";
import Dashboard from "./dashboard";
import "./design.css";
createRoot(document.getElementById("root")!).render(<React.StrictMode><Dashboard/></React.StrictMode>);
