import {Navigate,Outlet} from "react-router-dom"; import {useApp} from "../context/AppContext";
export default function ProtectedRoute(){return useApp().currentUser?<Outlet/>:<Navigate to="/login" replace/>}
