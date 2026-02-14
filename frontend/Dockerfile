FROM node:22-alpine AS base

WORKDIR /app
COPY package.json ./
RUN npm install

COPY . .

EXPOSE 3000

# Development
CMD ["npm", "run", "dev"]

# Production target
FROM base AS production
RUN npm run build
CMD ["npm", "start"]
