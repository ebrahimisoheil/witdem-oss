import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { Shell } from "./components";
import { ComparePage, DeveloperPage, GoalPerformancePage, IssuesPage, OverviewPage, RunsPage, SystemHealthPage } from "./pages";
import { WorkflowDefinitionPage, WorkflowDefinitionsPage, WorkflowEvaluationsPage, WorkflowExecutionPage, WorkflowExecutionsPage, WorkflowOperationsPage } from "./workflow-pages";
import "./styles.css";

// Recover an open tab whose previous deployment references a lazy chunk that
// no longer exists. Limit recovery to one reload per 30 seconds so a broken
// deployment still surfaces its error instead of creating a reload loop.
if (typeof window !== "undefined") {
  window.addEventListener("vite:preloadError", (event) => {
    event.preventDefault();
    const key = "witdem:preload-recovery";
    const previous = Number(window.sessionStorage.getItem(key) || 0);
    if (!previous || Date.now() - previous > 30_000) {
      window.sessionStorage.setItem(key, String(Date.now()));
      window.location.reload();
    }
  });
}

const root=createRootRoute({component:Shell});
const routes=[createRoute({getParentRoute:()=>root,path:"/",component:OverviewPage}),createRoute({getParentRoute:()=>root,path:"/system-health",component:SystemHealthPage}),createRoute({getParentRoute:()=>root,path:"/goal-performance",component:GoalPerformancePage}),createRoute({getParentRoute:()=>root,path:"/runs",component:RunsPage}),createRoute({getParentRoute:()=>root,path:"/compare",component:ComparePage}),createRoute({getParentRoute:()=>root,path:"/workflows",component:WorkflowDefinitionsPage}),createRoute({getParentRoute:()=>root,path:"/workflows/$workflowId",component:WorkflowDefinitionPage}),createRoute({getParentRoute:()=>root,path:"/workflows/$workflowId/operations",component:WorkflowOperationsPage}),createRoute({getParentRoute:()=>root,path:"/workflows/$workflowId/evaluations",component:WorkflowEvaluationsPage}),createRoute({getParentRoute:()=>root,path:"/workflows/$workflowId/executions",component:WorkflowExecutionsPage}),createRoute({getParentRoute:()=>root,path:"/workflows/$workflowId/executions/$executionId",component:WorkflowExecutionPage}),createRoute({getParentRoute:()=>root,path:"/issues",component:IssuesPage}),createRoute({getParentRoute:()=>root,path:"/developer",component:DeveloperPage})];
const router=createRouter({routeTree:root.addChildren(routes),defaultPreload:"intent"});
declare module "@tanstack/react-router" { interface Register { router: typeof router } }
const queryClient=new QueryClient({defaultOptions:{queries:{staleTime:15_000,retry:1}}});
ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><QueryClientProvider client={queryClient}><RouterProvider router={router}/></QueryClientProvider></React.StrictMode>);
