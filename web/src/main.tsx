import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { Shell } from "./components";
import { ComparePage, DeveloperPage, GoalPerformancePage, IssuesPage, OverviewPage, RunPage, RunsPage, SystemHealthPage, WorkflowsPage } from "./pages";
import "./styles.css";
import "@xyflow/react/dist/style.css";

const root=createRootRoute({component:Shell});
const routes=[createRoute({getParentRoute:()=>root,path:"/",component:OverviewPage}),createRoute({getParentRoute:()=>root,path:"/system-health",component:SystemHealthPage}),createRoute({getParentRoute:()=>root,path:"/goal-performance",component:GoalPerformancePage}),createRoute({getParentRoute:()=>root,path:"/runs",component:RunsPage}),createRoute({getParentRoute:()=>root,path:"/runs/$executionId",component:RunPage}),createRoute({getParentRoute:()=>root,path:"/compare",component:ComparePage}),createRoute({getParentRoute:()=>root,path:"/workflows",component:WorkflowsPage}),createRoute({getParentRoute:()=>root,path:"/issues",component:IssuesPage}),createRoute({getParentRoute:()=>root,path:"/developer",component:DeveloperPage})];
const router=createRouter({routeTree:root.addChildren(routes),defaultPreload:"intent"});
declare module "@tanstack/react-router" { interface Register { router: typeof router } }
const queryClient=new QueryClient({defaultOptions:{queries:{staleTime:15_000,retry:1}}});
ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><QueryClientProvider client={queryClient}><RouterProvider router={router}/></QueryClientProvider></React.StrictMode>);
