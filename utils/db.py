# utils/db.py
import mysql.connector.pooling
import logging
from config import DB_HOST, DB_USER, DB_PASSWORD, DB_NAME # Import database configurations

# Configure logging for this module
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Global variable for the database connection pool, initialized to None
_db_pool = None # Use a leading underscore to suggest it's internal to the module

def initialize_db_pool():
    """
    Initializes the global database connection pool using parameters from config.py.
    This function should be called once at application startup to ensure the pool
    is ready for use. Subsequent calls will not re-initialize the pool if it already exists.
    """
    global _db_pool
    if _db_pool is None:
        logger.info("Attempting to initialize database connection pool.")
        try:
            _db_pool = mysql.connector.pooling.MySQLConnectionPool(
                pool_name="infocall_app_pool",
                pool_size=5,  # Default pool size
                host=DB_HOST,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME
            )
            logger.info("Database connection pool initialized successfully.")
        except Exception as err:
            logger.error(f"Failed to initialize database pool: {err}", exc_info=True)
            raise

class DBConnectionManager:
    """
    A context manager for acquiring and releasing database connections from the pool.
    Ensures proper connection handling, including explicit transaction control and
    rollback on exceptions, and resource cleanup.
    """
    def __init__(self, dictionary_cursor=False):
        self.connection = None
        self.cursor = None
        self.dictionary_cursor = dictionary_cursor

    def __enter__(self):
        # Ensure the pool is initialized before attempting to get a connection
        if _db_pool is None:
            logger.error("Database pool is not initialized. Please call initialize_db_pool() before using DBConnectionManager.")
            raise Exception("Database pool is not available.")

        try:
            self.connection = _db_pool.get_connection()
            # Set autocommit to False for explicit transaction management
            self.connection.autocommit = False
            self.cursor = self.connection.cursor(dictionary=self.dictionary_cursor)
            logger.debug("Successfully acquired database connection and cursor from pool.")
            return self.cursor, self.connection
        except Exception as err:
            logger.error(f"Error acquiring database connection from pool: {err}", exc_info=True)
            # Ensure any acquired resources are closed if an error occurs during __enter__
            if self.cursor:
                try: self.cursor.close()
                except Exception as close_err: logger.warning(f"Error closing cursor during __enter__ error handling: {close_err}")
            if self.connection:
                try: self.connection.close() # Return connection to pool
                except Exception as close_err: logger.warning(f"Error closing connection during __enter__ error handling: {close_err}")
            raise # Re-raise the exception to propagate it

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.cursor:
            try:
                self.cursor.close()
                logger.debug("Database cursor closed.")
            except mysql.connector.Error as err:
                logger.warning(f"Error closing cursor: {err}")

        if self.connection:
            try:
                # Rollback transaction if an exception occurred within the 'with' block
                if exc_type is not None:
                    logger.warning(f"Exception of type {exc_type.__name__} occurred in 'with' block, attempting transaction rollback.")
                    try:
                        self.connection.rollback()
                        logger.info("Transaction rolled back successfully.")
                    except Exception as rb_err:
                        logger.error(f"Error during transaction rollback: {rb_err}", exc_info=True)
                # If no exception, it is expected that the user of this context manager
                # will explicitly call connection.commit() if changes need to be saved.
                # The connection is always returned to the pool.
                self.connection.close() # Returns the connection to the pool
                logger.debug("Database connection returned to pool.")
            except mysql.connector.Error as err:
                 logger.error(f"Error returning connection to pool: {err}", exc_info=True)
        return False # Propagate exceptions if any (True would suppress them)

def get_db_cursor(dictionary=False):
    """
    Convenience function to get a DBConnectionManager instance.
    Use this function with a 'with' statement to ensure proper
    database connection and cursor management.

    Example Usage:
    ```python
    from utils.db import get_db_cursor

    try:
        with get_db_cursor(dictionary=True) as (cursor, connection):
            cursor.execute("SELECT * FROM users")
            users = cursor.fetchall()
            print(users)
            connection.commit() # Remember to commit changes if needed
    except Exception as e:
        print(f"Database operation failed: {e}")
    ```

    Args:
        dictionary (bool): If True, the cursor will return results as dictionaries.
                           Otherwise, results are returned as tuples.

    Returns:
        DBConnectionManager: An instance of the context manager for database operations.
    """
    return DBConnectionManager(dictionary_cursor=dictionary)

# Initialize the database pool when this module is imported.
# In a Flask application, it's often more robust to call this from app.py
# (e.g., within an app context setup or a dedicated initialization function)
# to ensure it's precisely controlled during application startup.
# However, for self-containment and to address potential circular imports with 'app',
# initializing it here ensures the pool is ready as soon as 'db.py' is imported.
initialize_db_pool()