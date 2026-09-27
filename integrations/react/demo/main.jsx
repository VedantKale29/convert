// Demo page used by the end-to-end test: <UiGenerator /> mounted in a blank page.
import React from "react";
import { createRoot } from "react-dom/client";
import UiGenerator from "../src/UiGenerator.jsx";

createRoot(document.getElementById("root")).render(<UiGenerator />);
