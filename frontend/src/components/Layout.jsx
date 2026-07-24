import {useState} from "react"; import {Outlet} from "react-router-dom"; import Sidebar from "./Sidebar"; import Topbar from "./Topbar";
export default function Layout(){const [open,setOpen]=useState(false);return <div className="shell"><Sidebar open={open} onClose={()=>setOpen(false)}/><main><Topbar onMenu={()=>setOpen(true)}/><Outlet/></main></div>}
