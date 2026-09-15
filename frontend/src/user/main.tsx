import React from 'react';
import { createRoot } from 'react-dom/client';
import UserApp from './UserApp';
import '../styles/index.css';

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <UserApp />
  </React.StrictMode>,
);
