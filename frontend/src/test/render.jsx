import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

export function renderRoute(ui, route = "/dashboard", routePattern) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      {routePattern ? <Routes><Route path={routePattern} element={ui} /></Routes> : ui}
    </MemoryRouter>
  );
}
