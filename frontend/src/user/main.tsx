import React from 'react';
import { createRoot } from 'react-dom/client';
import UserApp from './UserApp';
import '../styles/index.css';
import '../styles/user.css';

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <UserApp />
  </React.StrictMode>,
);
