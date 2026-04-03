declare module "better-sqlite3" {
  class Database {
    constructor(path: string);
    pragma(pragma: string): unknown;
    exec(sql: string): void;
    prepare(sql: string): {
      run(...params: unknown[]): { changes: number; lastInsertRowid: number | bigint };
      get(...params: unknown[]): unknown;
      all(...params: unknown[]): unknown[];
    };
    close(): void;
  }
  export default Database;
}
