import React from "react";
import {createRoot} from "react-dom/client";
import Dashboard from "./dashboard";
import {LazyMotion,domMax,MotionConfig} from "motion/react";
import "./design.css";
import "./listing-motion.css";
import "./moderation.css";
import './member-features.css';
createRoot(document.getElementById("root")!).render(<React.StrictMode><MotionConfig reducedMotion="user"><LazyMotion features={domMax} strict><Dashboard/></LazyMotion></MotionConfig></React.StrictMode>);
