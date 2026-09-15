import React from 'react';
import { createRoot } from 'react-dom/client';
import AdminApp from './AdminApp';
import '../styles/index.css';

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AdminApp />
  </React.StrictMode>,
);
