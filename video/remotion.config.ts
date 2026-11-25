import path from "node:path";

import { Config } from "@remotion/cli/config";

// The clip imports the site's stage code from ../frontend. Those files must use this
// project's single copy of React, or the bundle carries two and hooks break.
Config.overrideWebpackConfig((config) => ({
  ...config,
  resolve: {
    ...config.resolve,
    alias: {
      ...(config.resolve?.alias ?? {}),
      react: path.resolve(process.cwd(), "node_modules/react"),
      "react-dom": path.resolve(process.cwd(), "node_modules/react-dom"),
    },
  },
}));
