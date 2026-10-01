import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

export function renderRoute(ui, route = "/dashboard", routePattern, locationState) {
  const entry = typeof route === "string" && locationState
    ? { pathname: route, state: locationState }
    : route;
  return render(
    <MemoryRouter initialEntries={[entry]}>
      {routePattern ? <Routes><Route path={routePattern} element={ui} /></Routes> : ui}
    </MemoryRouter>
  );
}
